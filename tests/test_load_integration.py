"""Integration tests against a real PostgreSQL database.

They create a throwaway database, so they never touch the warehouse. Set TEST_DATABASE_ADMIN_URL
to a server where the user may create databases (CI sets it); otherwise the tests are skipped.
"""
import datetime as dt
import os
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from etl import extract, load, quality  # noqa: E402
from etl.transform import parse_price_file, parse_registry_file  # noqa: E402

ADMIN_URL = os.environ.get("TEST_DATABASE_ADMIN_URL")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_DATABASE_ADMIN_URL not set")

REGISTRY = """Estrazione del 2026-07-01
idImpianto|Gestore|Bandiera|Tipo Impianto|Nome Impianto|Indirizzo|Comune|Provincia|Latitudine|Longitudine
1|G1|Q8|Stradale|A|via A|MILANO|MI|45.4|9.1
2|G2|Agip Eni|Autostradale|B|via B|ROMA|RM|41.9|12.5
"""


def day_file(day: str, price: str = "2.000") -> bytes:
    return (f"Estrazione del {day}\nidImpianto|descCarburante|prezzo|isSelf|dtComu\n"
            f"1|Benzina|{price}|1|{day[8:10]}/{day[5:7]}/{day[:4]} 07:00:00\n"
            f"2|Gasolio|2.200|0|{day[8:10]}/{day[5:7]}/{day[:4]} 07:00:00\n"
            f"999|Benzina|1.950|1|{day[8:10]}/{day[5:7]}/{day[:4]} 07:00:00\n"   # station not in registry
            f"1|Benzina|bad|0|{day[8:10]}/{day[5:7]}/{day[:4]} 07:00:00\n").encode()


@pytest.fixture()
def conn():
    name = f"etl_test_{uuid.uuid4().hex[:8]}"
    admin = psycopg2.connect(ADMIN_URL)
    admin.autocommit = True
    admin.cursor().execute(f"CREATE DATABASE {name}")
    url = ADMIN_URL.rsplit("/", 1)[0] + "/" + name
    c = load.connect(url)
    load.run_sql_file(c, "01_schema.sql")
    load.run_sql_file(c, "02_seed_regions.sql")
    yield c
    c.close()
    admin.cursor().execute(f"DROP DATABASE {name}")
    admin.close()


def count(conn, sql):
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchone()[0]


def load_bytes(conn, data, run_id):
    return load.load_price_file(conn, parse_price_file(data.decode()), run_id, "test", extract.sha256(data))


def test_full_day_load(conn):
    run = load.start_run(conn, "test")
    load.load_registry(conn, parse_registry_file(REGISTRY), run, "test")
    assert load_bytes(conn, day_file("2026-07-01"), run) == "loaded"
    assert count(conn, "SELECT COUNT(*) FROM fact_price") == 3
    assert count(conn, "SELECT COUNT(*) FROM etl_quarantine") == 1
    assert count(conn, "SELECT COUNT(*) FROM dim_station WHERE NOT in_registry") == 1   # station 999
    assert count(conn, "SELECT station_type FROM dim_station WHERE station_id = 2") == "Motorway"


def test_same_file_twice_is_skipped(conn):
    run = load.start_run(conn, "test")
    data = day_file("2026-07-01")
    load_bytes(conn, data, run)
    assert load.already_loaded(conn, extract.sha256(data))
    assert load_bytes(conn, data, run) == "skipped"
    assert count(conn, "SELECT COUNT(*) FROM fact_price") == 3
    assert count(conn, "SELECT COUNT(*) FROM etl_quarantine") == 1


def test_republished_file_replaces_the_day(conn):
    run = load.start_run(conn, "test")
    load_bytes(conn, day_file("2026-07-01", "2.000"), run)
    assert load_bytes(conn, day_file("2026-07-01", "2.050"), run) == "reloaded"
    assert count(conn, "SELECT COUNT(*) FROM fact_price") == 3
    assert count(conn, "SELECT price_eur::float FROM fact_price WHERE station_id = 1") == pytest.approx(2.05)
    assert count(conn, "SELECT COUNT(*) FROM etl_quarantine") == 1


def test_failed_load_leaves_nothing_behind(conn, monkeypatch):
    run = load.start_run(conn, "test")

    def boom(*a, **k):
        raise RuntimeError("simulated crash after the facts were written")
    monkeypatch.setattr(load, "_quarantine", boom)
    with pytest.raises(RuntimeError):
        load_bytes(conn, day_file("2026-07-01"), run)
    assert count(conn, "SELECT COUNT(*) FROM fact_price") == 0
    assert count(conn, "SELECT COUNT(*) FROM etl_loaded_file") == 0


def test_quality_report_finds_missing_day(conn):
    run = load.start_run(conn, "test")
    load_bytes(conn, day_file("2026-07-01"), run)
    load_bytes(conn, day_file("2026-07-03"), run)
    r = quality.report(conn)
    assert r["missing_days"] == [dt.date(2026, 7, 2)]
    assert r["loaded_days"]["count"] == 2
