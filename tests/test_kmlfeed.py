"""Tests for conflictwatch.kmlfeed — KML placemark input adapter.
Deterministic, offline; KML built from in-memory strings."""

from __future__ import annotations

from conflictwatch import kmlfeed
from conflictwatch.events import ConflictEvent


_KML_HEAD = '<?xml version="1.0" encoding="UTF-8"?>' \
            '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
_KML_TAIL = "</Document></kml>"


def _doc(*placemarks: str) -> str:
    return _KML_HEAD + "".join(placemarks) + _KML_TAIL


def _placemark(name="", desc="", coords="", data=None, when=""):
    parts = ["<Placemark>"]
    if name:
        parts.append(f"<name>{name}</name>")
    if desc:
        parts.append(f"<description>{desc}</description>")
    if when:
        parts.append(f"<TimeStamp><when>{when}</when></TimeStamp>")
    if data:
        parts.append("<ExtendedData>")
        for k, v in data.items():
            parts.append(f'<Data name="{k}"><value>{v}</value></Data>')
        parts.append("</ExtendedData>")
    if coords:
        parts.append(f"<Point><coordinates>{coords}</coordinates></Point>")
    parts.append("</Placemark>")
    return "".join(parts)


# --- basic parsing -----------------------------------------------------------
def test_parse_single_placemark():
    kml = _doc(_placemark(name="Strike site", desc="drone strike"))
    evs = kmlfeed.parse_kml(kml)
    assert len(evs) == 1 and isinstance(evs[0], ConflictEvent)


def test_parse_name_to_location():
    kml = _doc(_placemark(name="Kramatorsk", desc="drone strike"))
    assert kmlfeed.parse_kml(kml)[0].location == "Kramatorsk"


def test_parse_description_into_notes():
    kml = _doc(_placemark(name="Site", desc="a drone strike hit the depot"))
    assert "drone strike" in kmlfeed.parse_kml(kml)[0].notes


def test_parse_coordinates_lon_lat_order():
    kml = _doc(_placemark(name="X", coords="37.5,48.0,0"))
    ev = kmlfeed.parse_kml(kml)[0]
    assert ev.lon == 37.5 and ev.lat == 48.0


def test_parse_coordinates_no_altitude():
    kml = _doc(_placemark(name="X", coords="37.5,48.0"))
    ev = kmlfeed.parse_kml(kml)[0]
    assert ev.lon == 37.5 and ev.lat == 48.0


def test_parse_multiple_placemarks():
    kml = _doc(_placemark(name="A", desc="drone strike"),
               _placemark(name="B", desc="artillery shelling"))
    assert len(kmlfeed.parse_kml(kml)) == 2


def test_parse_event_type_from_text():
    kml = _doc(_placemark(name="Site", desc="an fpv drone attack"))
    assert kmlfeed.parse_kml(kml)[0].event_type == "drone/uas"


# --- ExtendedData ------------------------------------------------------------
def test_extended_data_date():
    kml = _doc(_placemark(name="X", desc="strike", data={"date": "2026-06-12"}))
    assert kmlfeed.parse_kml(kml)[0].date == "2026-06-12"


def test_extended_data_fatalities():
    kml = _doc(_placemark(name="X", desc="strike", data={"fatalities": "5"}))
    assert kmlfeed.parse_kml(kml)[0].fatalities == 5


def test_extended_data_actor_alias():
    # 'attacker' aliases to actor1 via events.normalize
    kml = _doc(_placemark(name="X", desc="strike",
                          data={"attacker": "Forces of Russia"}))
    assert kmlfeed.parse_kml(kml)[0].actor1 == "Forces of Russia"


def test_extended_data_overrides_derived():
    kml = _doc(_placemark(name="Placename", desc="drone strike",
                          data={"event_type": "battle"}))
    assert kmlfeed.parse_kml(kml)[0].event_type == "battle"


def test_extended_data_country():
    kml = _doc(_placemark(name="X", desc="strike", data={"country": "Ukraine"}))
    assert kmlfeed.parse_kml(kml)[0].country == "Ukraine"


def test_simpledata_schema_form():
    pm = ('<Placemark><name>X</name>'
          '<ExtendedData><SchemaData>'
          '<SimpleData name="fatalities">7</SimpleData>'
          '</SchemaData></ExtendedData></Placemark>')
    ev = kmlfeed.parse_kml(_doc(pm))[0]
    assert ev.fatalities == 7


# --- TimeStamp / TimeSpan ----------------------------------------------------
def test_timestamp_when_to_date():
    kml = _doc(_placemark(name="X", desc="strike", when="2026-06-12T10:00:00Z"))
    assert kmlfeed.parse_kml(kml)[0].date == "2026-06-12"


def test_timespan_begin_to_date():
    pm = ('<Placemark><name>X</name><description>strike</description>'
          '<TimeSpan><begin>2026-06-12</begin><end>2026-06-13</end></TimeSpan>'
          '</Placemark>')
    assert kmlfeed.parse_kml(_doc(pm))[0].date == "2026-06-12"


def test_extended_date_beats_timestamp():
    pm = ('<Placemark><name>X</name><description>strike</description>'
          '<TimeStamp><when>2026-06-12</when></TimeStamp>'
          '<ExtendedData><Data name="date"><value>2026-06-01</value></Data></ExtendedData>'
          '</Placemark>')
    assert kmlfeed.parse_kml(_doc(pm))[0].date == "2026-06-01"


# --- HTML / CDATA in description ---------------------------------------------
def test_description_html_stripped():
    kml = _doc(_placemark(name="X", desc="&lt;b&gt;drone strike&lt;/b&gt; hit the site"))
    notes = kmlfeed.parse_kml(kml)[0].notes
    assert "<b>" not in notes and "drone strike" in notes


def test_description_entities_decoded():
    kml = _doc(_placemark(name="X", desc="shelling &amp; fire"))
    assert "&" in kmlfeed.parse_kml(kml)[0].notes


# --- namespace tolerance & robustness ----------------------------------------
def test_bare_tags_without_namespace():
    kml = ("<kml><Document><Placemark><name>X</name>"
           "<description>drone strike</description>"
           "<Point><coordinates>37.5,48.0</coordinates></Point>"
           "</Placemark></Document></kml>")
    ev = kmlfeed.parse_kml(kml)[0]
    assert ev.lon == 37.5 and "drone strike" in ev.notes


def test_placemark_without_geometry():
    kml = _doc(_placemark(name="X", desc="drone strike, no coords"))
    ev = kmlfeed.parse_kml(kml)[0]
    assert ev.lat is None and ev.lon is None


def test_empty_string_returns_empty():
    assert kmlfeed.parse_kml("") == []


def test_whitespace_only_returns_empty():
    assert kmlfeed.parse_kml("   \n  ") == []


def test_malformed_xml_returns_empty():
    assert kmlfeed.parse_kml("<kml><Placemark><name>oops") == []


def test_no_placemarks_returns_empty():
    assert kmlfeed.parse_kml(_doc()) == []


def test_bad_coordinates_ignored():
    kml = _doc(_placemark(name="X", desc="strike", coords="not,a,number"))
    ev = kmlfeed.parse_kml(kml)[0]
    assert ev.lat is None and ev.lon is None


def test_single_coordinate_ignored():
    kml = _doc(_placemark(name="X", desc="strike", coords="37.5"))
    ev = kmlfeed.parse_kml(kml)[0]
    assert ev.lon is None


def test_source_label_applied():
    kml = _doc(_placemark(name="X", desc="strike"))
    assert kmlfeed.parse_kml(kml, source="tracker")[0].source == "tracker"


def test_source_from_extended_data_wins():
    kml = _doc(_placemark(name="X", desc="strike", data={"source": "ACLED"}))
    assert kmlfeed.parse_kml(kml, source="tracker")[0].source == "ACLED"


# --- is_kml sniff ------------------------------------------------------------
def test_is_kml_true_for_kml():
    assert kmlfeed.is_kml(_doc(_placemark(name="X")))


def test_is_kml_true_for_placemark_only():
    assert kmlfeed.is_kml("<Placemark><name>X</name></Placemark>")


def test_is_kml_false_for_json():
    assert not kmlfeed.is_kml('{"type": "FeatureCollection"}')


def test_is_kml_false_for_empty():
    assert not kmlfeed.is_kml("")


# --- to_kml / round-trip -----------------------------------------------------
def _ev(**kw):
    kw.setdefault("country", "Ukraine")
    return ConflictEvent(**kw)


def test_to_kml_contains_placemark():
    out = kmlfeed.to_kml([_ev(location="K", notes="drone strike")])
    assert "<Placemark>" in out and "<kml" in out


def test_to_kml_includes_point_for_geolocated():
    out = kmlfeed.to_kml([_ev(location="K", lat=48.0, lon=37.5)])
    assert "37.5,48.0" in out


def test_to_kml_no_point_without_coords():
    out = kmlfeed.to_kml([_ev(location="K", notes="no coords")])
    assert "<Point>" not in out


def test_to_kml_escapes_special_chars():
    out = kmlfeed.to_kml([_ev(location="A & B", notes="x < y")])
    assert "&amp;" in out and "&lt;" in out


def test_roundtrip_preserves_core_fields():
    src = [_ev(date="2026-06-12", event_type="drone/uas", location="Kramatorsk",
               lat=48.0, lon=37.5, fatalities=3, actor1="Forces of Russia",
               notes="drone strike kills three")]
    out = kmlfeed.to_kml(src)
    back = kmlfeed.parse_kml(out)
    assert len(back) == 1
    e = back[0]
    assert e.date == "2026-06-12" and e.fatalities == 3
    assert e.lat == 48.0 and e.lon == 37.5
    assert e.event_type == "drone/uas"


def test_roundtrip_multiple_events():
    src = [_ev(date="2026-06-12", location="A", lat=48.0, lon=37.5),
           _ev(date="2026-06-13", location="B", country="Sudan", event_type="battle")]
    back = kmlfeed.parse_kml(kmlfeed.to_kml(src))
    assert len(back) == 2


def test_roundtrip_empty():
    assert kmlfeed.parse_kml(kmlfeed.to_kml([])) == []
