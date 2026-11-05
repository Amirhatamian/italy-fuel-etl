"""Load: write parsed files into the PostgreSQL star schema.

Each daily price file is loaded in ONE transaction, and its SHA-256 fingerprint is stored
in etl_loaded_file. Running the same load twice is therefore safe:
  * same file again          -> skipped
  * file republished/changed -> that day is deleted and reloaded
  * error in the middle      -> nothing from that day is kept
"""
from __future__ import annotations

import datetime as dt
import io
import logging
import pathlib

import psycopg2
from psycopg2.extras import execute_values

from .transform import PriceFile, RegistryFile, fuel_group

log = logging.getLogger(__name__)
SQL_DIR = pathlib.Path(__file__).resolve().parent.parent / "sql"


def connect(url: str):
    conn = psycopg2.connect(url)
    with conn.cursor() as cur:
        cur.execute("SET search_path TO fuel_dw, public")
    return conn


def run_sql_file(conn, name: str, **params) -> None:
    sql = (SQL_DIR / name).read_text(encoding="utf-8")
    if params:
        sql = sql.format(**params)
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()


def date_key(day: dt.date) -> int:
    return day.year * 10000 + day.month * 100 + day.day


# ----------------------------------------------------------------- run log
def start_run(conn, command: str) -> int:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO etl_run (command) VALUES (%s) RETURNING run_id", (command,))
        run_id = cur.fetchone()[0]
    conn.commit()
    return run_id


def finish_run(conn, run_id: int, status: str, read: int = 0, loaded: int = 0,
               quarantined: int = 0, message: str | None = None) -> None:
    conn.rollback()  # discard any half-finished transaction before writing the outcome
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE etl_run SET finished_at = now(), status = %s, rows_read = %s,
                      rows_loaded = %s, rows_quarantined = %s, message = %s
               WHERE run_id = %s""",
            (status, read, loaded, quarantined, message, run_id))
    conn.commit()


# ----------------------------------------------------------------- dimensions
def ensure_dates(cur, start: dt.date, end: dt.date) -> None:
    cur.execute(
        """INSERT INTO dim_date
           SELECT TO_CHAR(d, 'YYYYMMDD')::int, d, EXTRACT(year FROM d), EXTRACT(quarter FROM d),
                  EXTRACT(month FROM d), EXTRACT(week FROM d), EXTRACT(isodow FROM d),
                  EXTRACT(isodow FROM d) IN (6, 7)
           FROM generate_series(%s::date, %s::date, interval '1 day') AS g(d)
           ON CONFLICT (date_key) DO NOTHING""", (start, end))


def load_registry(conn, reg: RegistryFile, run_id: int, source: str) -> int:
    """Insert or update stations. A newer registry snapshot overwrites older attributes."""
    with conn.cursor() as cur:
        cur.execute("SELECT province FROM dim_region")
        provinces = {r[0] for r in cur.fetchall()}
        rows = [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7] if r[7] in provinces else None,
                 r[8], r[9], True, reg.registry_date) for r in reg.rows]
        execute_values(cur, """
            INSERT INTO dim_station (station_id, operator, brand, station_type, station_name, address,
                                     municipality, province, latitude, longitude, in_registry, registry_date)
            VALUES %s
            ON CONFLICT (station_id) DO UPDATE SET
                operator = EXCLUDED.operator, brand = EXCLUDED.brand, station_type = EXCLUDED.station_type,
                station_name = EXCLUDED.station_name, address = EXCLUDED.address,
                municipality = EXCLUDED.municipality, province = EXCLUDED.province,
                latitude = EXCLUDED.latitude, longitude = EXCLUDED.longitude,
                in_registry = TRUE, registry_date = EXCLUDED.registry_date
            WHERE dim_station.registry_date IS NULL OR EXCLUDED.registry_date >= dim_station.registry_date""",
            rows, page_size=5000)
        _quarantine(cur, run_id, None, source, reg.quarantine)
    conn.commit()
    return len(rows)


def ensure_fuels(cur, names) -> dict[str, int]:
    execute_values(cur, "INSERT INTO dim_fuel (fuel_name, fuel_group) VALUES %s ON CONFLICT (fuel_name) DO NOTHING",
                   [(n, fuel_group(n)) for n in sorted(set(names))])
    cur.execute("SELECT fuel_name, fuel_id FROM dim_fuel")
    return dict(cur.fetchall())


def _quarantine(cur, run_id, price_date, source, rows) -> None:
    if rows:
        execute_values(cur, """INSERT INTO etl_quarantine (run_id, price_date, source, line_no, raw_line, reason)
                               VALUES %s""",
                       [(run_id, price_date, source, ln, raw, reason) for ln, raw, reason in rows])


# ----------------------------------------------------------------- facts
def already_loaded(conn, fingerprint: str) -> bool:
    """True if a file with exactly these bytes was loaded before (checked before parsing: fast)."""
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM etl_loaded_file WHERE sha256 = %s", (fingerprint,))
        found = cur.fetchone() is not None
    conn.rollback()
    return found


def load_price_file(conn, pf: PriceFile, run_id: int, source: str, fingerprint: str) -> str:
    """Load one day. Returns 'skipped', 'loaded' or 'reloaded'."""
    key = date_key(pf.price_date)
    with conn.cursor() as cur:
        cur.execute("SELECT sha256 FROM etl_loaded_file WHERE price_date = %s", (pf.price_date,))
        existing = cur.fetchone()
        if existing and existing[0] == fingerprint:
            conn.rollback()
            return "skipped"
        try:
            if existing:  # republished file: replace the whole day
                cur.execute("DELETE FROM fact_price WHERE date_key = %s", (key,))
                cur.execute("DELETE FROM etl_quarantine WHERE price_date = %s AND source = %s", (pf.price_date, source))
                cur.execute("DELETE FROM etl_loaded_file WHERE price_date = %s", (pf.price_date,))
            ensure_dates(cur, pf.price_date, pf.price_date)
            fuels = ensure_fuels(cur, (r[1] for r in pf.rows))

            cur.execute("""CREATE TEMP TABLE stg_price (station_id INT, fuel_id SMALLINT, is_self BOOLEAN,
                                                       price_eur NUMERIC(6,3), price_updated TIMESTAMP)
                           ON COMMIT DROP""")
            buf = io.StringIO()
            for sid, fuel, is_self, price, updated in pf.rows:
                buf.write(f"{sid}\t{fuels[fuel]}\t{'t' if is_self else 'f'}\t{price:.3f}\t{updated:%Y-%m-%d %H:%M:%S}\n")
            buf.seek(0)
            cur.copy_expert("COPY stg_price FROM STDIN", buf)

            # Stations that report prices but are missing from the registry snapshot
            cur.execute("""INSERT INTO dim_station (station_id, in_registry)
                           SELECT DISTINCT station_id, FALSE FROM stg_price
                           ON CONFLICT (station_id) DO NOTHING""")
            cur.execute("""INSERT INTO fact_price (date_key, station_id, fuel_id, is_self, price_eur, price_updated)
                           SELECT %s, station_id, fuel_id, is_self, price_eur, price_updated FROM stg_price""", (key,))
            loaded = cur.rowcount
            _quarantine(cur, run_id, pf.price_date, source, pf.quarantine)
            cur.execute("""INSERT INTO etl_loaded_file
                           (price_date, source, sha256, rows_read, rows_loaded, rows_quarantined, run_id)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        (pf.price_date, source, fingerprint, pf.rows_read, loaded, len(pf.quarantine), run_id))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return "reloaded" if existing else "loaded"


def refresh_station_dates(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""UPDATE dim_station s SET first_price_date = TO_DATE(x.mn::text, 'YYYYMMDD'),
                                                last_price_date  = TO_DATE(x.mx::text, 'YYYYMMDD')
                       FROM (SELECT station_id, MIN(date_key) mn, MAX(date_key) mx
                             FROM fact_price GROUP BY station_id) x
                       WHERE s.station_id = x.station_id""")
    conn.commit()


def load_market(conn, brent: dict, fx: dict, litres_per_barrel: float) -> int:
    days = sorted(set(brent) | set(fx))
    if not days:
        return 0
    with conn.cursor() as cur:
        ensure_dates(cur, days[0], days[-1])
        rows = []
        for d in days:
            b, r = brent.get(d), fx.get(d)
            rows.append((date_key(d), b, r, round(b / r / litres_per_barrel, 4) if b and r else None))
        execute_values(cur, """
            INSERT INTO fact_market (date_key, brent_usd_bbl, usd_per_eur, brent_eur_litre) VALUES %s
            ON CONFLICT (date_key) DO UPDATE SET brent_usd_bbl = EXCLUDED.brent_usd_bbl,
                usd_per_eur = EXCLUDED.usd_per_eur, brent_eur_litre = EXCLUDED.brent_eur_litre""", rows)
    conn.commit()
    return len(rows)
