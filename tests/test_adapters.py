"""Tests for conflictwatch.adapters — JSONL / GeoJSON / delimited / auto input adapters.
Deterministic, offline; every fixture is an in-memory string."""

from __future__ import annotations

import json

from conflictwatch import adapters
from conflictwatch.events import ConflictEvent
from conflictwatch.cli import main


# --- JSON Lines --------------------------------------------------------------
JSONL = (
    '{"date": "2026-06-01", "event_type": "battle", "country": "Ukraine", "fatalities": 3}\n'
    '{"date": "2026-06-02", "event_type": "drone/uas", "country": "Ukraine", "fatalities": 1}\n'
)


def test_jsonl_count():
    assert len(adapters.parse_jsonl(JSONL)) == 2


def test_jsonl_fields():
    evs = adapters.parse_jsonl(JSONL)
    assert evs[0].event_type == "battle" and evs[0].fatalities == 3


def test_jsonl_skips_blank():
    assert len(adapters.parse_jsonl(JSONL + "\n\n")) == 2


def test_jsonl_skips_comments():
    text = "# a comment\n" + JSONL
    assert len(adapters.parse_jsonl(text)) == 2


def test_jsonl_skips_malformed():
    text = JSONL + "{not valid json}\n"
    assert len(adapters.parse_jsonl(text)) == 2


def test_jsonl_source_from_record():
    text = '{"date":"2026-06-01","event_type":"battle","source":"MyFeed"}\n'
    assert adapters.parse_jsonl(text)[0].source == "MyFeed"


def test_jsonl_default_source():
    text = '{"date":"2026-06-01","event_type":"battle"}\n'
    assert adapters.parse_jsonl(text, source="feedX")[0].source == "feedX"


def test_jsonl_empty():
    assert adapters.parse_jsonl("") == []


def test_jsonl_returns_events():
    assert all(isinstance(e, ConflictEvent) for e in adapters.parse_jsonl(JSONL))


# --- GeoJSON -----------------------------------------------------------------
GEOJSON = json.dumps({
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [37.5, 48.0]},
         "properties": {"date": "2026-06-01", "event_type": "battle",
                        "country": "Ukraine", "fatalities": 4}},
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [30.5, 50.4]},
         "properties": {"date": "2026-06-02", "event_type": "drone/uas",
                        "country": "Ukraine"}},
    ],
})


def test_geojson_count():
    assert len(adapters.parse_geojson(GEOJSON)) == 2


def test_geojson_coords_lonlat_order():
    e = adapters.parse_geojson(GEOJSON)[0]
    assert e.lon == 37.5 and e.lat == 48.0


def test_geojson_properties():
    e = adapters.parse_geojson(GEOJSON)[0]
    assert e.country == "Ukraine" and e.fatalities == 4


def test_geojson_single_feature():
    feat = json.dumps({"type": "Feature",
                       "geometry": {"type": "Point", "coordinates": [10.0, 20.0]},
                       "properties": {"date": "2026-06-01", "event_type": "riots"}})
    evs = adapters.parse_geojson(feat)
    assert len(evs) == 1 and evs[0].lat == 20.0


def test_geojson_no_geometry():
    fc = json.dumps({"type": "FeatureCollection",
                     "features": [{"type": "Feature", "geometry": None,
                                   "properties": {"date": "2026-06-01",
                                                  "event_type": "protests"}}]})
    evs = adapters.parse_geojson(fc)
    assert len(evs) == 1 and evs[0].lat is None


def test_geojson_non_point_geometry():
    fc = json.dumps({"type": "FeatureCollection",
                     "features": [{"type": "Feature",
                                   "geometry": {"type": "Polygon", "coordinates": []},
                                   "properties": {"date": "2026-06-01",
                                                  "event_type": "battle"}}]})
    evs = adapters.parse_geojson(fc)
    assert len(evs) == 1 and evs[0].lat is None


def test_geojson_explicit_coords_not_overwritten():
    fc = json.dumps({"type": "FeatureCollection",
                     "features": [{"type": "Feature",
                                   "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
                                   "properties": {"date": "2026-06-01", "event_type": "battle",
                                                  "lat": 9.0, "lon": 8.0}}]})
    e = adapters.parse_geojson(fc)[0]
    assert e.lat == 9.0 and e.lon == 8.0


def test_geojson_empty_collection():
    fc = json.dumps({"type": "FeatureCollection", "features": []})
    assert adapters.parse_geojson(fc) == []


# --- delimited ---------------------------------------------------------------
def test_delimited_comma():
    text = "date,event_type,country,fatalities\n2026-06-01,battle,Ukraine,3\n"
    evs = adapters.parse_delimited(text)
    assert len(evs) == 1 and evs[0].fatalities == 3


def test_delimited_tab():
    text = "date\tevent_type\tcountry\n2026-06-01\tbattle\tSudan\n"
    evs = adapters.parse_delimited(text)
    assert evs[0].country == "Sudan"


def test_delimited_semicolon():
    text = "date;event_type;country\n2026-06-01;riots;Mali\n"
    assert adapters.parse_delimited(text)[0].event_type == "riots"


def test_delimited_pipe():
    text = "date|event_type|country\n2026-06-01|protests|Georgia\n"
    assert adapters.parse_delimited(text)[0].country == "Georgia"


def test_delimited_explicit_delimiter():
    text = "date;event_type\n2026-06-01;battle\n"
    assert len(adapters.parse_delimited(text, delimiter=";")) == 1


def test_delimited_empty():
    assert adapters.parse_delimited("") == []


def test_delimited_aliases_applied():
    # 'deaths' should alias to fatalities via normalize
    text = "event_date,type,deaths\n2026-06-01,battle,7\n"
    e = adapters.parse_delimited(text)[0]
    assert e.date == "2026-06-01" and e.fatalities == 7


# --- sniff_delimiter ---------------------------------------------------------
def test_sniff_comma():
    assert adapters.sniff_delimiter("a,b,c\n1,2,3") == ","


def test_sniff_tab():
    assert adapters.sniff_delimiter("a\tb\tc\n1\t2\t3") == "\t"


def test_sniff_semicolon():
    assert adapters.sniff_delimiter("a;b;c") == ";"


def test_sniff_default_comma():
    assert adapters.sniff_delimiter("singlecolumn") == ","


# --- sniff_format ------------------------------------------------------------
def test_sniff_format_geojson():
    assert adapters.sniff_format(GEOJSON) == "geojson"


def test_sniff_format_json():
    assert adapters.sniff_format('[{"a":1}]') == "json"


def test_sniff_format_jsonl():
    assert adapters.sniff_format(JSONL) == "jsonl"


def test_sniff_format_delimited():
    assert adapters.sniff_format("a,b,c\n1,2,3") == "delimited"


def test_sniff_format_empty():
    assert adapters.sniff_format("   ") == "empty"


# --- parse_auto --------------------------------------------------------------
def test_parse_auto_geojson():
    evs = adapters.parse_auto(GEOJSON)
    assert len(evs) == 2 and evs[0].lat == 48.0


def test_parse_auto_jsonl():
    assert len(adapters.parse_auto(JSONL)) == 2


def test_parse_auto_json_list():
    text = '[{"date":"2026-06-01","event_type":"battle","country":"Ukraine"}]'
    assert len(adapters.parse_auto(text)) == 1


def test_parse_auto_delimited():
    text = "date,event_type,country\n2026-06-01,battle,Ukraine\n"
    assert len(adapters.parse_auto(text)) == 1


def test_parse_auto_empty():
    assert adapters.parse_auto("") == []


# --- to_jsonl round trip -----------------------------------------------------
def test_to_jsonl_roundtrip():
    evs = adapters.parse_jsonl(JSONL)
    text = adapters.to_jsonl(evs)
    again = adapters.parse_jsonl(text)
    assert len(again) == len(evs)
    assert again[0].event_type == evs[0].event_type


def test_to_jsonl_line_count():
    evs = adapters.parse_jsonl(JSONL)
    assert adapters.to_jsonl(evs).count("\n") == len(evs) - 1


# --- parse dispatch ----------------------------------------------------------
def test_parse_dispatch_jsonl():
    assert len(adapters.parse("jsonl", JSONL)) == 2


def test_parse_dispatch_auto():
    assert len(adapters.parse("auto", GEOJSON)) == 2


def test_parse_dispatch_unknown():
    import pytest
    with pytest.raises(ValueError):
        adapters.parse("xml", "<x/>")


# --- CLI ---------------------------------------------------------------------
def _run(argv, capsys):
    rc = main(argv)
    return rc, capsys.readouterr().out


def test_cli_adapt_geojson(tmp_path, capsys):
    p = tmp_path / "in.geojson"
    p.write_text(GEOJSON, encoding="utf-8")
    rc, out = _run(["adapt", str(p), "--format", "geojson"], capsys)
    assert rc == 0
    assert len(json.loads(out)) == 2


def test_cli_adapt_auto(tmp_path, capsys):
    p = tmp_path / "in.jsonl"
    p.write_text(JSONL, encoding="utf-8")
    rc, out = _run(["adapt", str(p)], capsys)
    assert rc == 0
    assert len(json.loads(out)) == 2


def test_cli_adapt_out(tmp_path, capsys):
    p = tmp_path / "in.csv"
    o = tmp_path / "out.json"
    p.write_text("date,event_type,country\n2026-06-01,battle,Ukraine\n", encoding="utf-8")
    rc, out = _run(["adapt", str(p), "--format", "delimited", "--out", str(o)], capsys)
    assert rc == 0 and o.exists()
    assert json.loads(o.read_text(encoding="utf-8"))[0]["event_type"] == "battle"
