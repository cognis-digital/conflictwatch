"""adapters — extra open-source input formats -> normalized ConflictEvents.

`sources` covers the three canonical datasets (ACLED CSV, GDELT TSV, UCDP CSV) plus a
generic JSON list. Real OSINT collection also shows up as **JSON Lines** (one event per
line, the streaming/export lingua franca), **GeoJSON** ``FeatureCollection`` (what most map
tools and many trackers emit), and arbitrary **delimited** exports whose separator you
don't know ahead of time. This module adds those adapters, plus a format **sniffer** and a
one-call ``parse_auto`` that picks the right one.

All adapters route every record through :func:`conflictwatch.events.normalize`, so field
aliasing, type coercion and id assignment stay identical to the rest of the pipeline. Pure
standard library, deterministic, offline.
"""

from __future__ import annotations

import csv
import io
import json

from conflictwatch.events import ConflictEvent, normalize


# --------------------------------------------------------------------------- #
# JSON Lines
# --------------------------------------------------------------------------- #
def parse_jsonl(text: str, source: str = "jsonl") -> list[ConflictEvent]:
    """One JSON object per line -> events. Blank lines and ``#`` comments are skipped;
    a malformed line is skipped rather than aborting the whole stream."""
    out: list[ConflictEvent] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec if isinstance(rec, ConflictEvent)
                       else normalize(rec, source=rec.get("source") or source))
    return out


# --------------------------------------------------------------------------- #
# GeoJSON FeatureCollection
# --------------------------------------------------------------------------- #
def parse_geojson(text: str, source: str = "geojson") -> list[ConflictEvent]:
    """A GeoJSON ``FeatureCollection`` (or bare Feature/geometry list) -> events.

    Each feature's ``properties`` become the record; a Point ``geometry`` supplies
    ``lon, lat`` (GeoJSON order is [lon, lat]). Features without usable geometry still
    parse from their properties. Non-Point geometries contribute no coordinate.
    """
    data = json.loads(text)
    if isinstance(data, dict) and data.get("type") == "FeatureCollection":
        features = data.get("features", [])
    elif isinstance(data, dict) and data.get("type") == "Feature":
        features = [data]
    elif isinstance(data, list):
        features = data
    else:
        features = [data]

    out: list[ConflictEvent] = []
    for feat in features:
        if not isinstance(feat, dict):
            continue
        props = dict(feat.get("properties") or {})
        geom = feat.get("geometry") or {}
        if isinstance(geom, dict) and geom.get("type") == "Point":
            coords = geom.get("coordinates") or []
            if len(coords) >= 2:
                props.setdefault("lon", coords[0])
                props.setdefault("lat", coords[1])
        out.append(normalize(props, source=props.get("source") or source))
    return out


# --------------------------------------------------------------------------- #
# delimiter-sniffing tabular
# --------------------------------------------------------------------------- #
def parse_delimited(text: str, source: str = "delimited",
                    delimiter: str | None = None) -> list[ConflictEvent]:
    """A header + rows in an *unknown* delimiter (comma/tab/semicolon/pipe) -> events.

    When ``delimiter`` is None the separator is sniffed from the header line. Uses the
    standard ``csv`` reader so quoting is honored. Field names are aliased by
    :func:`normalize` exactly as the ACLED adapter does.
    """
    text = text or ""
    if not text.strip():
        return []
    if delimiter is None:
        delimiter = sniff_delimiter(text)
    out: list[ConflictEvent] = []
    for row in csv.DictReader(io.StringIO(text), delimiter=delimiter):
        clean = {k: v for k, v in row.items() if k is not None}
        out.append(normalize(clean, source=clean.get("source") or source))
    return out


def sniff_delimiter(text: str) -> str:
    """Guess the column delimiter from the header line among ``, \\t ; |`` (defaults to ,)."""
    head = ""
    for line in (text or "").splitlines():
        if line.strip():
            head = line
            break
    best, best_count = ",", -1
    for cand in (",", "\t", ";", "|"):
        c = head.count(cand)
        if c > best_count:
            best, best_count = cand, c
    return best if best_count > 0 else ","


# --------------------------------------------------------------------------- #
# format sniffing + dispatch
# --------------------------------------------------------------------------- #
def sniff_format(text: str) -> str:
    """Best-effort format label: ``geojson`` | ``json`` | ``jsonl`` | ``delimited`` | ``empty``."""
    t = (text or "").lstrip()
    if not t:
        return "empty"
    if t[0] in "[{":
        # could be a single JSON value, JSONL of objects, or GeoJSON
        try:
            data = json.loads(t)
            if isinstance(data, dict) and data.get("type") in ("FeatureCollection", "Feature"):
                return "geojson"
            return "json"
        except json.JSONDecodeError:
            # multiple JSON objects one-per-line?
            lines = [ln for ln in t.splitlines() if ln.strip()]
            if len(lines) > 1 and all(ln.lstrip().startswith("{") for ln in lines[:5]):
                return "jsonl"
            return "json"
    return "delimited"


def parse_auto(text: str, source: str = "auto") -> list[ConflictEvent]:
    """Sniff the format and route to the right adapter. Empty input -> ``[]``.

    ``json`` is delegated to :func:`conflictwatch.sources.parse_generic_json` so the
    canonical JSON path stays authoritative.
    """
    fmt = sniff_format(text)
    if fmt == "empty":
        return []
    if fmt == "geojson":
        return parse_geojson(text, source=source)
    if fmt == "jsonl":
        return parse_jsonl(text, source=source)
    if fmt == "json":
        from conflictwatch.sources import parse_generic_json
        return parse_generic_json(text)
    return parse_delimited(text, source=source)


# --------------------------------------------------------------------------- #
# events -> JSON Lines (round-trips with parse_jsonl)
# --------------------------------------------------------------------------- #
def to_jsonl(events) -> str:
    """Serialize events as JSON Lines (one ``to_dict()`` object per line)."""
    return "\n".join(json.dumps(e.to_dict(), ensure_ascii=False) for e in events)


ADAPTERS = {"jsonl": parse_jsonl, "geojson": parse_geojson,
            "delimited": parse_delimited, "auto": parse_auto}


def parse(fmt: str, text: str) -> list[ConflictEvent]:
    """Parse ``text`` with the named adapter (``jsonl``/``geojson``/``delimited``/``auto``)."""
    if fmt not in ADAPTERS:
        raise ValueError(f"unknown adapter {fmt!r}; expected {sorted(ADAPTERS)}")
    return ADAPTERS[fmt](text)
