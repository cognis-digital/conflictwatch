"""Native, dependency-free intel export for conflictwatch events.

Turns normalized :class:`~conflictwatch.events.ConflictEvent` records into shareable
formats so a situational picture drops straight into the tools analysts use:

* **GeoJSON** — every geolocated event as a point for Leaflet/Mapbox/QGIS/kepler.gl
  (conflict mapping is the primary analyst workflow).
* **STIX 2.1** — a valid bundle pairing a ``location`` SDO with an ``observed-data``
  + ``note`` per event, grouped in a ``report``; ingestible by TIPs/OpenCTI.

Standard library only — complements :mod:`conflictwatch.connect` (which forwards a
Finding stream via the optional cognis-connect SDK). Descriptive, open-source
situational awareness only; this models *reported* events, not targeting.
"""

from __future__ import annotations

import json
import uuid

from .events import ConflictEvent

_NS = uuid.UUID("c0117100-0000-4000-8000-636f676e6973")
_FALLBACK_TS = "2026-01-01T00:00:00.000Z"


def _events(data) -> list[ConflictEvent]:
    """Accept a list of ConflictEvent or of plain dicts; normalize to events."""
    fields = set(ConflictEvent.__dataclass_fields__)  # severity is a property, excluded
    out = []
    for r in (data or []):
        if isinstance(r, ConflictEvent):
            out.append(r)
        elif isinstance(r, dict):
            out.append(ConflictEvent(**{k: v for k, v in r.items() if k in fields}))
    return out


def _ts(e: ConflictEvent) -> str:
    d = (e.date or "").strip()
    if len(d) == 10:  # YYYY-MM-DD
        return f"{d}T00:00:00.000Z"
    return _FALLBACK_TS


# --------------------------------------------------------------------------- #
# GeoJSON
# --------------------------------------------------------------------------- #
def to_geojson(events) -> str:
    feats = []
    for e in _events(events):
        if e.lat is None or e.lon is None:
            continue
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [e.lon, e.lat]},  # [lon,lat]
            "properties": {
                "date": e.date, "event_type": e.event_type, "severity": e.severity,
                "actor1": e.actor1, "actor2": e.actor2, "country": e.country,
                "region": e.region, "location": e.location, "fatalities": e.fatalities,
                "source": e.source, "source_url": e.source_url, "notes": e.notes,
            },
        })
    return json.dumps({"type": "FeatureCollection", "features": feats}, indent=2)


# --------------------------------------------------------------------------- #
# STIX 2.1
# --------------------------------------------------------------------------- #
def to_stix(events) -> str:
    objects: list[dict] = []
    report_refs: list[str] = []
    evs = _events(events)

    for e in evs:
        seed = json.dumps(e.to_dict(), sort_keys=True, default=str)
        ts = _ts(e)
        note_id = f"note--{uuid.uuid5(_NS, 'note:' + seed)}"
        obs_id = f"observed-data--{uuid.uuid5(_NS, 'obs:' + seed)}"
        obj_refs = []

        if e.lat is not None and e.lon is not None:
            loc_id = f"location--{uuid.uuid5(_NS, f'loc:{e.lat},{e.lon}:{e.location}')}"
            objects.append({
                "type": "location", "spec_version": "2.1", "id": loc_id,
                "created": ts, "modified": ts,
                "latitude": e.lat, "longitude": e.lon,
                **({"country": e.country} if e.country else {}),
                **({"region": e.region} if e.region else {}),
                **({"name": e.location} if e.location else {}),
            })
            report_refs.append(loc_id)
            obj_refs.append(loc_id)

        desc = (f"{e.event_type} in {e.country}"
                f"{(' (' + e.location + ')') if e.location else ''} on {e.date or 'unknown date'}; "
                f"{e.fatalities} reported fatalities."
                + (f" Actors: {e.actor1}" + (f" vs {e.actor2}" if e.actor2 else "") if e.actor1 else ""))
        objects.append({
            "type": "observed-data", "spec_version": "2.1", "id": obs_id,
            "created": ts, "modified": ts,
            "first_observed": ts, "last_observed": ts, "number_observed": 1,
            "object_refs": obj_refs or [note_id],
        })
        objects.append({
            "type": "note", "spec_version": "2.1", "id": note_id,
            "created": ts, "modified": ts,
            "abstract": (e.notes or e.event_type)[:120],
            "content": desc,
            "labels": [e.event_type, e.severity] + ([e.country] if e.country else []),
            "object_refs": [obs_id] + obj_refs,
            **({"external_references": [{"source_name": e.source or "conflictwatch",
                                         "url": e.source_url}]} if e.source_url else {}),
        })
        report_refs.extend([obs_id, note_id])

    report_id = f"report--{uuid.uuid5(_NS, 'report:' + '|'.join(report_refs))}"
    report = {
        "type": "report", "spec_version": "2.1", "id": report_id,
        "created": _FALLBACK_TS, "modified": _FALLBACK_TS,
        "name": f"conflictwatch situational report ({len(evs)} events)",
        "report_types": ["threat-report"],
        "published": _FALLBACK_TS,
        "object_refs": report_refs or [report_id],
    }
    bundle = {
        "type": "bundle",
        "id": f"bundle--{uuid.uuid5(_NS, report_id)}",
        "objects": [report] + objects,
    }
    return json.dumps(bundle, indent=2)


_EXPORTERS = {"geojson": to_geojson, "stix": to_stix}


def export(events, fmt: str) -> str:
    fmt = fmt.lower()
    if fmt not in _EXPORTERS:
        raise ValueError(f"unknown export format {fmt!r}; choose one of {sorted(_EXPORTERS)}")
    return _EXPORTERS[fmt](events)
