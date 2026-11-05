"""Unit tests for parsing and validation (no database needed)."""
import datetime as dt

import pytest

from etl.transform import SchemaError, fuel_group, parse_price_file, parse_registry_file

HEADER = "idImpianto{s}descCarburante{s}prezzo{s}isSelf{s}dtComu"


def price_file(rows, sep="|", day="2026-07-01"):
    return "\n".join([f"Estrazione del {day}", HEADER.format(s=sep), *rows]) + "\n"


@pytest.mark.parametrize("sep", ["|", ";"])
def test_both_separators_are_supported(sep):
    text = price_file([f"3464{sep}Benzina{sep}2.239{sep}1{sep}26/06/2026 19:00:20"], sep=sep)
    pf = parse_price_file(text)
    assert pf.price_date == dt.date(2026, 7, 1)
    assert pf.rows == [(3464, "Benzina", True, 2.239, dt.datetime(2026, 6, 26, 19, 0, 20))]
    assert pf.quarantine == []


@pytest.mark.parametrize("line, reason", [
    ("3464|Benzina|2.239|1", "wrong number of fields"),
    ("abc|Benzina|2.239|1|26/06/2026 19:00:20", "station id not a number"),
    ("3464||2.239|1|26/06/2026 19:00:20", "missing fuel name"),
    ("3464|Benzina|due|1|26/06/2026 19:00:20", "price not a number"),
    ("3464|Benzina|0.001|1|26/06/2026 19:00:20", "price out of range"),
    ("3464|Benzina|9.999|1|26/06/2026 19:00:20", "price out of range"),
    ("3464|Benzina|2.239|2|26/06/2026 19:00:20", "isSelf not 0/1"),
    ("3464|Benzina|2.239|1|2026-06-26", "bad timestamp"),
    ("3464|Benzina|2.239|1|05/07/2026 10:00:00", "timestamp after extraction date"),
])
def test_bad_rows_are_quarantined_with_a_reason(line, reason):
    pf = parse_price_file(price_file([line]))
    assert pf.rows == []
    assert len(pf.quarantine) == 1
    assert pf.quarantine[0][2].startswith(reason)
    assert pf.quarantine[0][0] == 3          # line number in the file


def test_duplicate_key_keeps_first_and_quarantines_second():
    rows = ["1|Benzina|2.000|1|30/06/2026 08:00:00", "1|Benzina|2.100|1|30/06/2026 09:00:00"]
    pf = parse_price_file(price_file(rows))
    assert len(pf.rows) == 1 and pf.rows[0][3] == 2.0
    assert pf.quarantine[0][2] == "duplicate station/fuel/service"


def test_good_rows_survive_next_to_bad_ones():
    rows = ["1|Benzina|2.000|1|30/06/2026 08:00:00", "2|Benzina|x|1|30/06/2026 08:00:00",
            "3|Gasolio|2.200|0|30/06/2026 08:00:00"]
    pf = parse_price_file(price_file(rows))
    assert pf.rows_read == 3 and len(pf.rows) == 2 and len(pf.quarantine) == 1


def test_changed_header_stops_the_load():
    text = "Estrazione del 2026-07-01\nidImpianto|carburante|prezzo|isSelf|dtComu\n"
    with pytest.raises(SchemaError):
        parse_price_file(text)


def test_missing_title_line_stops_the_load():
    with pytest.raises(SchemaError):
        parse_price_file("idImpianto|descCarburante|prezzo|isSelf|dtComu\n1|Benzina|2|1|01/07/2026 08:00:00\n")


def test_registry_repairs_website_tag_with_separator_inside():
    header = "idImpianto|Gestore|Bandiera|Tipo Impianto|Nome Impianto|Indirizzo|Comune|Provincia|Latitudine|Longitudine"
    row = ("40820|STOIL SIMPLE|Pompe Bianche|Stradale|STOIL SIMPLE | gestori.prezzibenzina.it|"
           "STR. PROV.LE 82 15122|ALESSANDRIA|AL|44.917|8.700")
    reg = parse_registry_file(f"Estrazione del 2026-11-02\n{header}\n{row}\n")
    assert reg.quarantine == []
    station = reg.rows[0]
    assert station[0] == 40820 and station[4] == "STOIL SIMPLE" and station[3] == "Road"
    assert station[7] == "AL" and station[8] == pytest.approx(44.917)


def test_registry_missing_coordinates_become_none():
    header = "idImpianto|Gestore|Bandiera|Tipo Impianto|Nome Impianto|Indirizzo|Comune|Provincia|Latitudine|Longitudine"
    reg = parse_registry_file(f"Estrazione del 2026-11-02\n{header}\n7|G|Q8|Autostradale|N|A|C|MI||\n")
    assert reg.rows[0][8] is None and reg.rows[0][3] == "Motorway"


@pytest.mark.parametrize("name, group", [
    ("Benzina", "Petrol"), ("Gasolio", "Diesel"), ("GPL", "LPG"), ("Metano", "CNG"), ("L-GNC", "CNG"),
    ("GNL", "LNG"), ("HVOlution", "HVO"), ("Gasolio Bio HVO", "HVO"), ("Blue Diesel", "Premium diesel"),
    ("Hi-Q Diesel", "Premium diesel"), ("Blue Super", "Premium petrol"), ("HiQ Perform+", "Premium petrol"),
    ("V-Power", "Premium petrol"), ("V-Power Diesel", "Premium diesel"), ("Benzina WR 100", "Premium petrol"),
    ("F101", "Other"),
])
def test_fuel_groups(name, group):
    assert fuel_group(name) == group
