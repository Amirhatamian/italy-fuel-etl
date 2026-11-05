"""Command-line interface.  Run `python -m etl --help`.

Typical use:
    python -m etl init-db
    python -m etl load-registry --file data/registry/anagrafica_impianti_attivi_20261102.csv
    python -m etl backfill --from 2025-4 --to 2026-3
    python -m etl load-market --start 2025-10-01 --end 2026-09-30
    python -m etl refresh-marts
    python -m etl check
    python -m etl run-daily          # every morning: registry + today's prices + market + marts
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys

from . import extract, load, quality
from .config import load_config
from .transform import SchemaError, parse_price_file, parse_registry_file

log = logging.getLogger("etl")


def cmd_init_db(cfg, conn, args):
    load.run_sql_file(conn, "01_schema.sql")
    load.run_sql_file(conn, "02_seed_regions.sql")
    print("schema ready")


def _load_registry_bytes(cfg, conn, data: bytes, source: str):
    run_id = load.start_run(conn, f"load-registry {source}")
    try:
        reg = parse_registry_file(data.decode("utf-8"))
        n = load.load_registry(conn, reg, run_id, source)
        load.finish_run(conn, run_id, "success", reg.rows_read, n, len(reg.quarantine))
        print(f"registry {reg.registry_date}: {n:,} stations, {len(reg.quarantine)} quarantined")
    except Exception as e:
        load.finish_run(conn, run_id, "failed", message=str(e)[:500])
        raise


def cmd_load_registry(cfg, conn, args):
    if args.file:
        data, source = extract.fetch(args.file), args.file
    else:
        source = cfg["sources"]["mimit_daily_registry"]
        data = extract.fetch(source, cfg["paths"]["download_dir"], f"registry_{dt.date.today()}.csv", use_cache=False)
    _load_registry_bytes(cfg, conn, data, source)


def _load_day(cfg, conn, run_id, data: bytes, source: str):
    fingerprint = extract.sha256(data)
    if load.already_loaded(conn, fingerprint):
        return None, "skipped"
    v = cfg["validation"]
    pf = parse_price_file(data.decode("utf-8"), v["min_price_eur"], v["max_price_eur"])
    status = load.load_price_file(conn, pf, run_id, source, fingerprint)
    return pf, status


def cmd_backfill(cfg, conn, args):
    totals = {"read": 0, "loaded": 0, "quarantined": 0, "loaded_days": 0, "skipped_days": 0}
    run_id = load.start_run(conn, f"backfill {args.start} {args.end}")
    try:
        for year, q in extract.quarters_between(args.start, args.end):
            url = cfg["sources"]["archive_url_template"].format(year=year, quarter=q)
            archive = extract.fetch(url, cfg["paths"]["download_dir"], f"{year}_{q}_tr.tar.gz")
            for day, name, data in extract.archive_days(archive):
                pf, status = _load_day(cfg, conn, run_id, data, f"archive {year}-{q}")
                if status == "skipped":
                    totals["skipped_days"] += 1
                else:
                    totals["loaded_days"] += 1
                    totals["read"] += pf.rows_read
                    totals["loaded"] += len(pf.rows)
                    totals["quarantined"] += len(pf.quarantine)
                log.info("%s %s", day, status)
        load.refresh_station_dates(conn)
        msg = f"{totals['loaded_days']} days loaded, {totals['skipped_days']} skipped (already loaded)"
        load.finish_run(conn, run_id, "success", totals["read"], totals["loaded"], totals["quarantined"], msg)
        print(msg, f"| rows loaded {totals['loaded']:,}, quarantined {totals['quarantined']:,}")
    except (Exception, KeyboardInterrupt) as e:
        load.finish_run(conn, run_id, "failed", totals["read"], totals["loaded"], totals["quarantined"], str(e)[:500])
        raise


def cmd_load_day(cfg, conn, args):
    run_id = load.start_run(conn, "load-day")
    try:
        if args.file:
            data, source = extract.fetch(args.file), args.file
        else:
            source = cfg["sources"]["mimit_daily_prices"]
            data = extract.fetch(source, cfg["paths"]["download_dir"], f"prices_{dt.date.today()}.csv", use_cache=False)
        pf, status = _load_day(cfg, conn, run_id, data, source)
        if pf is None:
            load.finish_run(conn, run_id, "success", message="file already loaded")
            print("skipped: this exact file is already loaded")
            return
        load.finish_run(conn, run_id, "success", pf.rows_read, len(pf.rows), len(pf.quarantine),
                        f"{pf.price_date} {status}")
        print(f"{pf.price_date}: {status} ({len(pf.rows):,} rows, {len(pf.quarantine)} quarantined)")
    except Exception as e:
        load.finish_run(conn, run_id, "failed", message=str(e)[:500])
        raise


def cmd_load_market(cfg, conn, args):
    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    run_id = load.start_run(conn, f"load-market {start} {end}")
    try:
        fx = extract.ecb_eurusd(cfg["sources"]["ecb_eurusd"], start, end)
        brent = extract.brent_daily(cfg["sources"]["brent_daily"], start, end)
        n = load.load_market(conn, brent, fx, cfg["marts"]["litres_per_barrel"])
        load.finish_run(conn, run_id, "success", n, n, 0, f"{len(brent)} Brent days, {len(fx)} FX days")
        print(f"market: {len(brent)} Brent days, {len(fx)} EUR/USD days → {n} rows")
    except Exception as e:
        load.finish_run(conn, run_id, "failed", message=str(e)[:500])
        raise


def cmd_refresh_marts(cfg, conn, args):
    m = cfg["marts"]
    load.run_sql_file(conn, "03_marts.sql", **m)
    print("marts refreshed")


def cmd_check(cfg, conn, args):
    r = quality.report(conn, cfg["validation"]["max_daily_row_change"])
    quality.print_report(r)
    if args.strict and (r["missing_days"] or r["volume_anomalies"]):
        sys.exit(1)


def cmd_run_daily(cfg, conn, args):
    cmd_load_registry(cfg, conn, argparse.Namespace(file=None))
    cmd_load_day(cfg, conn, argparse.Namespace(file=None))
    today = dt.date.today()
    cmd_load_market(cfg, conn, argparse.Namespace(start=str(today - dt.timedelta(days=10)), end=str(today)))
    cmd_refresh_marts(cfg, conn, args)


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m etl", description="Italian fuel price data pipeline")
    p.add_argument("--config", help="path to config.yaml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="create schema and reference data")
    s = sub.add_parser("load-registry", help="load the station registry (file or today's download)")
    s.add_argument("--file")
    s = sub.add_parser("backfill", help="load quarterly archives, e.g. --from 2025-4 --to 2026-3")
    s.add_argument("--from", dest="start", required=True)
    s.add_argument("--to", dest="end", required=True)
    s = sub.add_parser("load-day", help="load one daily price file (file or today's download)")
    s.add_argument("--file")
    s = sub.add_parser("load-market", help="load Brent and EUR/USD for a date range")
    s.add_argument("--start", required=True)
    s.add_argument("--end", required=True)
    sub.add_parser("refresh-marts", help="rebuild analysis views")
    s = sub.add_parser("check", help="data quality report")
    s.add_argument("--strict", action="store_true", help="exit with code 1 if problems are found")
    sub.add_parser("run-daily", help="daily job: registry + prices + market + marts")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(args.config)
    conn = load.connect(cfg["database"]["url"])
    try:
        globals()[f"cmd_{args.command.replace('-', '_')}"](cfg, conn, args)
    except SchemaError as e:
        print(f"STOPPED: file structure changed, nothing loaded for this file: {e}", file=sys.stderr)
        sys.exit(2)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
