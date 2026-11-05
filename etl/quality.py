"""Data quality report on what has been loaded: gaps, volume anomalies, quarantine, unknown stations."""
from __future__ import annotations


def report(conn, max_daily_row_change: float = 0.20) -> dict:
    out = {}
    with conn.cursor() as cur:
        cur.execute("SELECT MIN(price_date), MAX(price_date), COUNT(*) FROM etl_loaded_file")
        first, last, n = cur.fetchone()
        out["loaded_days"] = {"first": first, "last": last, "count": n}

        cur.execute("""SELECT d::date FROM generate_series(
                           (SELECT MIN(price_date) FROM etl_loaded_file),
                           (SELECT MAX(price_date) FROM etl_loaded_file), interval '1 day') d
                       WHERE d::date NOT IN (SELECT price_date FROM etl_loaded_file)
                       ORDER BY 1""")
        out["missing_days"] = [r[0] for r in cur.fetchall()]

        cur.execute("""SELECT price_date, rows_loaded, prev,
                              ROUND(100.0 * (rows_loaded - prev) / prev, 1) AS pct_change
                       FROM (SELECT price_date, rows_loaded,
                                    LAG(rows_loaded) OVER (ORDER BY price_date) AS prev
                             FROM etl_loaded_file) x
                       WHERE prev IS NOT NULL AND ABS(rows_loaded - prev) > %s * prev
                       ORDER BY price_date""", (max_daily_row_change,))
        out["volume_anomalies"] = cur.fetchall()

        cur.execute("""SELECT SPLIT_PART(reason, ' (', 1) AS reason, COUNT(*)
                       FROM etl_quarantine GROUP BY 1 ORDER BY 2 DESC""")
        out["quarantine_by_reason"] = cur.fetchall()

        cur.execute("""SELECT COUNT(*) FILTER (WHERE in_registry), COUNT(*) FILTER (WHERE NOT in_registry)
                       FROM dim_station""")
        known, unknown = cur.fetchone()
        out["stations"] = {"in_registry": known, "not_in_registry": unknown}

        cur.execute("""SELECT SUM(rows_read), SUM(rows_loaded), SUM(rows_quarantined) FROM etl_loaded_file""")
        out["price_rows"] = dict(zip(["read", "loaded", "quarantined"], cur.fetchone()))

        cur.execute("""SELECT run_id, command, status, started_at::timestamp(0), rows_loaded, message
                       FROM etl_run ORDER BY run_id DESC LIMIT 5""")
        out["last_runs"] = cur.fetchall()
    return out


def print_report(r: dict) -> None:
    d = r["loaded_days"]
    print(f"Loaded days: {d['count']} ({d['first']} → {d['last']})")
    print(f"Missing days: {len(r['missing_days'])} {[str(x) for x in r['missing_days']]}")
    pr = r["price_rows"]
    if pr["read"]:
        print(f"Price rows: read {pr['read']:,} · loaded {pr['loaded']:,} · quarantined {pr['quarantined']:,} "
              f"({100 * pr['quarantined'] / pr['read']:.3f}%)")
    print("Quarantine by reason:", r["quarantine_by_reason"])
    print("Stations:", r["stations"])
    print(f"Volume anomalies (row count change above threshold): {len(r['volume_anomalies'])}")
    for row in r["volume_anomalies"]:
        print("   ", row)
    print("Last runs:")
    for row in r["last_runs"]:
        print("   ", row)
