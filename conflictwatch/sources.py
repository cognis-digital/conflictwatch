"""Adapters for real open-source conflict datasets -> normalized ConflictEvents.

  acled     ACLED export CSV          (the standard armed-conflict event dataset)
  gdelt     GDELT 2.0 events TSV      (global event stream, machine-coded from news)
  ucdp      UCDP GED CSV              (Uppsala conflict deaths dataset)
  json      a generic JSON list/{events:[...]} from any tool

All are *open* datasets (ACLED/UCDP require free registration for bulk; GDELT is fully
open). Network fetch is best-effort and respects each provider's terms — prefer
`--from-file` with an export you pulled. Pure standard library.
"""

from __future__ import annotations

import csv
import io
import json
import urllib.request

from conflictwatch.events import ConflictEvent, normalize

# fully-open, no-key endpoints (others need a key/registration -> use --from-file)
SOURCES = {
    "gdelt": "http://data.gdeltproject.org/gdeltv2/lastupdate.txt",   # pointer to latest TSV
}


def parse_acled_csv(text: str) -> list[ConflictEvent]:
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        out.append(normalize(row, source=row.get("source") or "ACLED"))
    return out


def parse_ucdp_csv(text: str) -> list[ConflictEvent]:
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        # UCDP GED uses date_start, best (best fatality estimate), side_a/side_b
        rec = dict(row)
        rec.setdefault("date", row.get("date_start", ""))
        rec.setdefault("fatalities", row.get("best", row.get("deaths_a", 0)))
        out.append(normalize(rec, source="UCDP GED"))
    return out


# GDELT 2.0 column indices we use (the file has 61 tab-separated columns, no header)
_G = {"date": 1, "actor1": 6, "actor2": 16, "geo_full": 52, "lat": 56, "lon": 57, "url": 60}


def parse_gdelt_tsv(text: str) -> list[ConflictEvent]:
    out = []
    for line in text.splitlines():
        c = line.split("\t")
        if len(c) < 58:
            continue

        def g(k):
            i = _G[k]
            return c[i] if i < len(c) else ""
        loc = g("geo_full")
        country = loc.split(",")[-1].strip() if loc else ""
        out.append(normalize({
            "date": g("date"), "actor1": g("actor1"), "actor2": g("actor2"),
            "location": loc.split(",")[0].strip() if loc else "", "country": country,
            "lat": g("lat"), "lon": g("lon"), "source_url": g("url"),
            "notes": f"{g('actor1')} / {g('actor2')} @ {loc}".strip(" /@"),
        }, source="GDELT"))
    return out


def parse_generic_json(text: str) -> list[ConflictEvent]:
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("events") or data.get("data") or data.get("results") or [data]
    return [r if isinstance(r, ConflictEvent) else normalize(r, source="json") for r in data]


PARSERS = {"acled": parse_acled_csv, "ucdp": parse_ucdp_csv,
           "gdelt": parse_gdelt_tsv, "json": parse_generic_json}


def parse(source: str, text: str) -> list[ConflictEvent]:
    if source not in PARSERS:
        raise ValueError(f"unknown source {source!r}; expected {sorted(PARSERS)}")
    return PARSERS[source](text)


def fetch_gdelt_latest(timeout: float = 60.0) -> list[ConflictEvent]:
    """Pull GDELT's most recent 15-minute events export (open, no key)."""
    with urllib.request.urlopen(SOURCES["gdelt"], timeout=timeout) as r:
        pointer = r.read().decode("utf-8", "replace")
    url = next((p for p in pointer.split() if p.endswith(".export.CSV.zip")), None)
    if not url:
        raise RuntimeError("could not locate GDELT export URL in lastupdate pointer")
    import zipfile
    with urllib.request.urlopen(url, timeout=timeout) as r:
        blob = r.read()
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        text = z.read(z.namelist()[0]).decode("utf-8", "replace")
    return parse_gdelt_tsv(text)
