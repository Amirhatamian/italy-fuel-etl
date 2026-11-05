"""Parse and validate raw MIMIT files. Pure functions (no database), so they are easy to test.

Problems handled (all seen in the real files):
  * line 1 is a title, "Estrazione del YYYY-MM-DD", not the header
  * the separator changed from ";" (2025) to "|" (since 2026) -> detected per file
  * station names containing a website tag with the separator inside (registry file)
  * tab characters inside names
  * any row that breaks a rule is QUARANTINED with a reason, never silently dropped
  * an unexpected header (schema change) stops the run instead of loading wrong columns
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

PRICE_HEADER = ["idimpianto", "desccarburante", "prezzo", "isself", "dtcomu"]
REGISTRY_HEADER = ["idimpianto", "gestore", "bandiera", "tipo impianto", "nome impianto",
                   "indirizzo", "comune", "provincia", "latitudine", "longitudine"]
WEBSITE_TAG = " | gestori.prezzibenzina.it"


class SchemaError(ValueError):
    """The file does not have the expected structure; loading it would be unsafe."""


@dataclass
class PriceFile:
    price_date: dt.date
    rows: list[tuple] = field(default_factory=list)        # (station_id, fuel, is_self, price, updated)
    quarantine: list[tuple] = field(default_factory=list)  # (line_no, raw_line, reason)
    rows_read: int = 0


@dataclass
class RegistryFile:
    registry_date: dt.date
    rows: list[tuple] = field(default_factory=list)        # 10 station attributes
    quarantine: list[tuple] = field(default_factory=list)
    rows_read: int = 0


def _title_date(line: str) -> dt.date:
    parts = line.strip().split()
    if len(parts) < 3 or parts[0].lower() != "estrazione":
        raise SchemaError(f"first line is not 'Estrazione del <date>': {line[:60]!r}")
    return dt.date.fromisoformat(parts[-1])


def _separator(header: str) -> str:
    if "|" in header:
        return "|"
    if ";" in header:
        return ";"
    raise SchemaError(f"cannot detect separator in header: {header[:80]!r}")


def _check_header(found: list[str], expected: list[str]) -> None:
    if [h.strip().lower() for h in found] != expected:
        raise SchemaError(f"unexpected header {found} (expected {expected})")


def parse_price_file(text: str, min_price: float = 0.10, max_price: float = 5.00) -> PriceFile:
    lines = text.splitlines()
    if len(lines) < 2:
        raise SchemaError("file has no header")
    result = PriceFile(price_date=_title_date(lines[0]))
    sep = _separator(lines[1])
    _check_header(lines[1].split(sep), PRICE_HEADER)
    latest_allowed = dt.datetime.combine(result.price_date + dt.timedelta(days=1), dt.time())
    seen = set()

    for line_no, raw in enumerate(lines[2:], start=3):
        if not raw.strip():
            continue
        result.rows_read += 1
        parts = [p.strip() for p in raw.split(sep)]
        reason = None
        if len(parts) != 5:
            reason = f"wrong number of fields ({len(parts)})"
        else:
            sid, fuel, price, is_self, updated = parts
            try:
                sid = int(sid)
            except ValueError:
                reason = "station id not a number"
            if reason is None and not fuel:
                reason = "missing fuel name"
            if reason is None:
                try:
                    price = float(price.replace(",", "."))
                except ValueError:
                    reason = "price not a number"
            if reason is None and not (min_price <= price <= max_price):
                reason = f"price out of range ({price})"
            if reason is None and is_self not in ("0", "1"):
                reason = f"isSelf not 0/1 ({is_self!r})"
            if reason is None:
                try:
                    updated = dt.datetime.strptime(updated, "%d/%m/%Y %H:%M:%S")
                except ValueError:
                    reason = "bad timestamp"
            if reason is None and updated > latest_allowed:
                reason = "timestamp after extraction date"
            if reason is None:
                key = (sid, fuel, is_self == "1")
                if key in seen:
                    reason = "duplicate station/fuel/service"
                else:
                    seen.add(key)
        if reason:
            result.quarantine.append((line_no, raw[:500], reason))
        else:
            result.rows.append((sid, fuel, is_self == "1", round(price, 3), updated))
    return result


def parse_registry_file(text: str) -> RegistryFile:
    lines = text.splitlines()
    result = RegistryFile(registry_date=_title_date(lines[0]))
    sep = _separator(lines[1])
    _check_header(lines[1].split(sep), REGISTRY_HEADER)
    seen = set()
    for line_no, raw in enumerate(lines[2:], start=3):
        if not raw.strip():
            continue
        result.rows_read += 1
        clean = raw.replace(WEBSITE_TAG, "").replace("\t", " ")
        parts = [p.strip() or None for p in clean.split(sep)]
        reason = None
        if len(parts) != 10:
            reason = f"wrong number of fields ({len(parts)})"
        else:
            try:
                parts[0] = int(parts[0])
                parts[8] = float(parts[8]) if parts[8] else None
                parts[9] = float(parts[9]) if parts[9] else None
            except (TypeError, ValueError):
                reason = "id or coordinates not numeric"
            if reason is None and parts[0] in seen:
                reason = "duplicate station id"
        if reason:
            result.quarantine.append((line_no, raw[:500], reason))
        else:
            seen.add(parts[0])
            parts[3] = {"Autostradale": "Motorway", "Stradale": "Road"}.get(parts[3], parts[3])
            result.rows.append(tuple(parts))
    return result


def fuel_group(name: str) -> str:
    """Map the ~40 published fuel names to a few comparable groups."""
    n = name.lower().strip()
    if n == "benzina":
        return "Petrol"
    if n == "gasolio":
        return "Diesel"
    if n in ("gpl",):
        return "LPG"
    if n in ("metano", "l-gnc", "gnc"):
        return "CNG"
    if n in ("gnl", "l-gnl"):
        return "LNG"
    if "hvo" in n:
        return "HVO"
    if "diesel" in n or "gasolio" in n:
        return "Premium diesel"
    premium_petrol_words = ("benzina", "super", "verde", "hiq perform", "v-power", "wr 100")
    if any(w in n for w in premium_petrol_words):
        return "Premium petrol"
    return "Other"
