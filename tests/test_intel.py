"""Native intel export — STIX 2.1 bundle + GeoJSON for conflict events."""

import json

from conflictwatch.events import ConflictEvent
from conflictwatch import intel


def _events():
    return [
        ConflictEvent(date="2026-06-01", event_type="battle", actor1="Force A",
                      actor2="Force B", country="Examplestan", region="North",
                      location="Rivertown", lat=34.5, lon=44.2, fatalities=12,
                      source="ACLED", source_url="https://example.org/e1",
                      notes="Sustained clashes near the river crossing."),
        ConflictEvent(date="2026-06-02", event_type="drone/uas", country="Examplestan",
                      location="Hill 401", lat=34.6, lon=44.3, fatalities=0, source="OSINT"),
        ConflictEvent(date="2026-06-03", event_type="protests", country="Examplestan",
                      notes="No coordinates on this one."),  # no lat/lon
    ]


def test_geojson_only_geolocated():
    doc = json.loads(intel.to_geojson(_events()))
    assert doc["type"] == "FeatureCollection"
    assert len(doc["features"]) == 2  # the no-coord event is skipped
    f = doc["features"][0]
    assert f["geometry"]["type"] == "Point"
    lon, lat = f["geometry"]["coordinates"]
    assert (lon, lat) == (44.2, 34.5)  # [lon, lat] order
    assert f["properties"]["event_type"] == "battle"


def test_stix_bundle_valid():
    doc = json.loads(intel.to_stix(_events()))
    assert doc["type"] == "bundle"
    assert doc["id"].startswith("bundle--")
    types = {o["type"] for o in doc["objects"]}
    assert {"report", "observed-data", "note", "location"} <= types
    for o in doc["objects"]:
        assert o["id"].startswith(o["type"] + "--")
        if o["type"] != "bundle":
            assert o.get("spec_version") == "2.1"


def test_stix_report_refs_resolve():
    doc = json.loads(intel.to_stix(_events()))
    objs = {o["id"] for o in doc["objects"]}
    report = next(o for o in doc["objects"] if o["type"] == "report")
    for ref in report["object_refs"]:
        assert ref in objs


def test_stix_location_has_coords():
    doc = json.loads(intel.to_stix(_events()))
    locs = [o for o in doc["objects"] if o["type"] == "location"]
    assert locs and locs[0]["latitude"] == 34.5 and locs[0]["longitude"] == 44.2


def test_deterministic():
    assert intel.to_stix(_events()) == intel.to_stix(_events())
    assert intel.to_geojson(_events()) == intel.to_geojson(_events())


def test_accepts_dicts():
    dicts = [e.to_dict() for e in _events()]
    doc = json.loads(intel.to_geojson(dicts))
    assert len(doc["features"]) == 2


def test_export_dispatch_and_error():
    import pytest
    assert json.loads(intel.export(_events(), "geojson"))["type"] == "FeatureCollection"
    assert json.loads(intel.export(_events(), "stix"))["type"] == "bundle"
    with pytest.raises(ValueError):
        intel.export(_events(), "csv")
