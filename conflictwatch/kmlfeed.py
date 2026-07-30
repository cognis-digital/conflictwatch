"""kmlfeed — KML placemark input adapter -> normalized ConflictEvents.

`adapters` covers JSONL, GeoJSON and sniffed-delimited input; `reports` can *emit* KML.
The missing direction is *reading* KML: it is the lingua franca of GEOINT and mapping tools
(Google Earth, many open trackers and monitoring dashboards export placemarks), so an analyst
frequently starts from a ``.kml`` of geolocated reports. This module parses those placemarks
into the same normalized event model as every other source.

Each ``<Placemark>`` becomes one record:

  * ``<name>``                       -> notes / location hint
  * ``<description>``                 -> notes (HTML tags stripped to text)
  * ``<Point><coordinates>``         -> lon, lat (KML order is lon,lat[,alt])
  * ``<ExtendedData>`` Data/SimpleData -> extra fields (date, actor, fatalities, ...),
                                          aliased by :func:`conflictwatch.events.normalize`
  * ``<TimeStamp>``/``<TimeSpan>``   -> date, when no explicit date field is present

Namespace-tolerant (KML 2.2 and bare tags both parse). Descriptive collection of *reported*
geolocated placemarks for awareness — it reads open map exports, it geolocates nothing new and
targets nothing. Pure standard library, deterministic, offline.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from conflictwatch.events import ConflictEvent, normalize

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _local(tag: str) -> str:
    """Strip any ``{namespace}`` prefix from an element tag, lowercased."""
    return tag.rsplit("}", 1)[-1].lower()


def _find(el, name: str):
    """First descendant whose local tag equals ``name`` (namespace-agnostic)."""
    for child in el.iter():
        if _local(child.tag) == name:
            return child
    return None


def _text(el, name: str) -> str:
    node = _find(el, name)
    if node is None or node.text is None:
        return ""
    return node.text.strip()


def _strip_html(s: str) -> str:
    """Best-effort plain text from an HTML/CDATA description (tags removed, ws collapsed)."""
    if not s:
        return ""
    txt = _TAG_RE.sub(" ", s)
    txt = (txt.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
              .replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " "))
    return _WS_RE.sub(" ", txt).strip()


def _coords(placemark) -> tuple[float | None, float | None]:
    """First Point ``lon,lat`` from a placemark's ``<coordinates>`` (KML order), else (None, None)."""
    node = _find(placemark, "coordinates")
    if node is None or not (node.text or "").strip():
        return None, None
    first = (node.text or "").strip().split()[0]
    parts = first.split(",")
    if len(parts) < 2:
        return None, None
    try:
        lon = float(parts[0])
        lat = float(parts[1])
    except ValueError:
        return None, None
    return lon, lat


def _extended_data(placemark) -> dict:
    """Flatten ``<ExtendedData>`` Data/@name and SimpleData/@name into a field dict."""
    out: dict = {}
    ext = _find(placemark, "extendeddata")
    if ext is None:
        return out
    for child in ext.iter():
        tag = _local(child.tag)
        if tag in ("data", "simpledata"):
            key = child.get("name")
            if not key:
                continue
            if tag == "data":
                value_node = None
                for gc in child:
                    if _local(gc.tag) == "value":
                        value_node = gc
                        break
                val = (value_node.text if value_node is not None else child.text) or ""
            else:
                val = child.text or ""
            val = val.strip()
            if val:
                out[key.strip()] = val
    return out


def _timestamp(placemark) -> str:
    """A date from ``<TimeStamp><when>`` or ``<TimeSpan><begin>`` (first 10 chars), else ""."""
    when = _find(placemark, "when")
    if when is not None and (when.text or "").strip():
        return when.text.strip()
    begin = _find(placemark, "begin")
    if begin is not None and (begin.text or "").strip():
        return begin.text.strip()
    return ""


def parse_placemark(placemark, source: str = "kml") -> ConflictEvent:
    """Turn one ``<Placemark>`` element into a normalized :class:`ConflictEvent`.

    ExtendedData fields win over the derived name/description so an explicit ``date`` or
    ``fatalities`` column is honored; coordinates come from the Point geometry.
    """
    name = _text(placemark, "name")
    desc = _strip_html(_text(placemark, "description"))
    lon, lat = _coords(placemark)
    record: dict = {}
    if name:
        record["location"] = name
    notes = " — ".join(p for p in (name, desc) if p) if (name and desc) else (name or desc)
    if notes:
        record["notes"] = notes
    if lon is not None and lat is not None:
        record["lon"] = lon
        record["lat"] = lat
    ts = _timestamp(placemark)
    if ts:
        record["date"] = ts
    # ExtendedData is authoritative — overlay it last
    record.update(_extended_data(placemark))
    # let the event-type heuristics see the descriptive text
    if "event_type" not in record and notes:
        record["event_type"] = notes
    return normalize(record, source=record.get("source") or source)


def parse_kml(text: str, source: str = "kml") -> list[ConflictEvent]:
    """Parse a KML document string into events (one per ``<Placemark>``).

    Namespace-tolerant and resilient: a malformed document returns ``[]`` rather than
    raising, and placemarks without geometry still parse from their name/description/data.
    """
    text = (text or "").strip()
    if not text:
        return []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    out: list[ConflictEvent] = []
    for el in root.iter():
        if _local(el.tag) == "placemark":
            out.append(parse_placemark(el, source=source))
    return out


def is_kml(text: str) -> bool:
    """Heuristic sniff: does this text look like a KML document?"""
    head = (text or "").lstrip()[:512].lower()
    return "<kml" in head or ("<placemark" in head) or "opengis.net/kml" in head


def to_kml(events, *, name: str = "conflictwatch") -> str:
    """Serialize events to a minimal KML ``Document`` (round-trips with :func:`parse_kml`).

    Geolocated events get a ``<Point>``; the event's key fields ride along as
    ``<ExtendedData>`` so a parse-emit-parse cycle preserves them. Descriptive export only.
    """
    def esc(s: str) -> str:
        return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;"))

    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<kml xmlns="http://www.opengis.net/kml/2.2">',
             f"<Document><name>{esc(name)}</name>"]
    for e in events:
        parts.append("<Placemark>")
        pm_name = e.location or e.event_type or "event"
        parts.append(f"<name>{esc(pm_name)}</name>")
        if e.notes:
            parts.append(f"<description>{esc(e.notes)}</description>")
        if e.date:
            parts.append(f"<TimeStamp><when>{esc(e.date)}</when></TimeStamp>")
        parts.append("<ExtendedData>")
        for field in ("date", "event_type", "actor1", "actor2", "country", "region",
                      "fatalities", "source"):
            val = getattr(e, field, "")
            if val not in (None, "", 0):
                parts.append(f'<Data name="{field}"><value>{esc(val)}</value></Data>')
        parts.append("</ExtendedData>")
        if e.lon is not None and e.lat is not None:
            parts.append(f"<Point><coordinates>{e.lon},{e.lat}</coordinates></Point>")
        parts.append("</Placemark>")
    parts.append("</Document></kml>")
    return "\n".join(parts)
