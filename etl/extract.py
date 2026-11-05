"""Extract: get raw files from the web (or a local path) and keep a copy in the download folder."""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import logging
import pathlib
import re
import tarfile
import urllib.request
from typing import Iterator

log = logging.getLogger(__name__)
USER_AGENT = "italy-fuel-etl/1.0 (open-data pipeline)"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(source: str, download_dir: str | None = None, filename: str | None = None,
          use_cache: bool = True) -> bytes:
    """Read a local file, or download a URL (cached in download_dir)."""
    if not source.startswith(("http://", "https://")):
        return pathlib.Path(source).read_bytes()
    cached = pathlib.Path(download_dir) / filename if download_dir and filename else None
    if cached and use_cache and cached.exists():
        log.info("using cached %s", cached)
        return cached.read_bytes()
    log.info("downloading %s", source)
    req = urllib.request.Request(source, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=300) as r:
        data = r.read()
    if cached:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
    return data


def archive_days(archive_bytes: bytes) -> Iterator[tuple[dt.date, str, bytes]]:
    """Yield (date, member name, file bytes) for each daily price file in a quarterly archive, in date order."""
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tar:
        members = []
        for m in tar.getmembers():
            match = re.search(r"prezzo_alle_8-(\d{8})\.csv$", m.name)
            if m.isfile() and match:
                members.append((dt.datetime.strptime(match.group(1), "%Y%m%d").date(), m))
        for day, m in sorted(members, key=lambda x: x[0]):
            yield day, m.name, tar.extractfile(m).read()


def quarters_between(start: str, end: str) -> list[tuple[int, int]]:
    """'2025-4', '2026-3' -> [(2025, 4), (2026, 1), (2026, 2), (2026, 3)]"""
    y, q = map(int, start.split("-"))
    y2, q2 = map(int, end.split("-"))
    out = []
    while (y, q) <= (y2, q2):
        out.append((y, q))
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    return out


def ecb_eurusd(url_template: str, start: dt.date, end: dt.date) -> dict[dt.date, float]:
    data = fetch(url_template.format(start=start.isoformat(), end=end.isoformat())).decode("utf-8")
    rows = csv.DictReader(io.StringIO(data))
    return {dt.date.fromisoformat(r["TIME_PERIOD"]): float(r["OBS_VALUE"]) for r in rows if r["OBS_VALUE"]}


def brent_daily(url: str, start: dt.date, end: dt.date) -> dict[dt.date, float]:
    data = fetch(url).decode("utf-8")
    out = {}
    for r in csv.DictReader(io.StringIO(data)):
        day = dt.date.fromisoformat(r["Date"])
        if start <= day <= end and r["Price"]:
            out[day] = float(r["Price"])
    return out
