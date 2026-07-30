"""Tests for conflictwatch.extract — entity/event extraction from free OSINT text.
Deterministic, offline; every case is built from in-memory strings."""

from __future__ import annotations

import json

from conflictwatch import extract
from conflictwatch.events import ConflictEvent
from conflictwatch.cli import main


# --- casualties --------------------------------------------------------------
def test_casualties_number_before_killed():
    assert extract.extract_casualties("6 killed in shelling")["killed"] == 6


def test_casualties_killed_before_number():
    assert extract.extract_casualties("the strike killed 9 people")["killed"] == 9


def test_casualties_at_least_qualifier():
    assert extract.extract_casualties("at least 25 killed")["killed"] == 25


def test_casualties_wounded():
    r = extract.extract_casualties("3 killed and 12 wounded")
    assert r["killed"] == 3 and r["wounded"] == 12


def test_casualties_injured_synonym():
    assert extract.extract_casualties("two dead, seven injured")["wounded"] == 7


def test_casualties_number_words():
    assert extract.extract_casualties("six killed")["killed"] == 6


def test_casualties_dozen_word():
    assert extract.extract_casualties("a dozen wounded in the blast")["wounded"] == 12


def test_casualties_max_wins():
    # two different killed counts in one text -> the larger is taken
    assert extract.extract_casualties("initially 3 killed, later 8 killed")["killed"] == 8


def test_casualties_comma_thousands():
    assert extract.extract_casualties("1,200 killed over the offensive")["killed"] == 1200


def test_casualties_none():
    assert extract.extract_casualties("a protest was held downtown") == {"killed": 0, "wounded": 0}


def test_casualties_empty():
    assert extract.extract_casualties("") == {"killed": 0, "wounded": 0}


def test_casualties_people_between():
    assert extract.extract_casualties("5 people were killed")["killed"] == 5


# --- dates -------------------------------------------------------------------
def test_dates_iso():
    assert extract.extract_dates("event on 2026-06-12 near town") == ["2026-06-12"]


def test_dates_day_month_year():
    assert extract.extract_dates("on 12 June 2026 forces advanced") == ["2026-06-12"]


def test_dates_month_day_year():
    assert extract.extract_dates("June 12, 2026 saw clashes") == ["2026-06-12"]


def test_dates_slash():
    assert extract.extract_dates("dated 06/12/2026") == ["2026-06-12"]


def test_dates_multiple_ordered():
    ds = extract.extract_dates("from 2026-06-01 through 2026-06-05")
    assert ds == ["2026-06-01", "2026-06-05"]


def test_dates_dedup():
    assert extract.extract_dates("2026-06-01 and again 2026-06-01") == ["2026-06-01"]


def test_dates_none():
    assert extract.extract_dates("no date here") == []


def test_first_date():
    assert extract.first_date("reported 2026-06-09, updated 2026-06-10") == "2026-06-09"


def test_first_date_empty():
    assert extract.first_date("undated wire copy") == ""


# --- platforms ---------------------------------------------------------------
def test_platform_fpv():
    assert "fpv-drone" in extract.extract_platforms("an FPV drone strike")


def test_platform_shahed():
    assert "shahed-loitering-munition" in extract.extract_platforms("Shahed drones overnight")


def test_platform_generic_drone():
    assert "drone-uas" in extract.extract_platforms("a UAV was downed")


def test_platform_artillery():
    assert "artillery" in extract.extract_platforms("heavy shelling reported")


def test_platform_missile():
    assert "missile-rocket" in extract.extract_platforms("a cruise missile struck the depot")


def test_platform_airstrike():
    assert "air-delivered" in extract.extract_platforms("an airstrike hit the market")


def test_platform_ied():
    assert "ied-mine" in extract.extract_platforms("a roadside IED detonated")


def test_platform_ew():
    assert "electronic-warfare" in extract.extract_platforms("GPS jamming disrupted navigation")


def test_platform_dedup_and_order():
    plats = extract.extract_platforms("FPV drone and another FPV drone plus a UAV")
    assert plats.count("fpv-drone") == 1


def test_platform_none():
    assert extract.extract_platforms("a peaceful protest") == []


# --- actors ------------------------------------------------------------------
def test_actor_forces_of():
    assert "Forces of Russia" in extract.extract_actors("Forces of Russia advanced")


def test_actor_forces_of_stops_at_verb():
    a = extract.extract_actors("Forces of Ukraine struck the depot")
    assert "Forces of Ukraine" in a


def test_actor_unit_word():
    a = extract.extract_actors("the 53rd Mechanized Brigade held the line")
    assert any("Brigade" in x for x in a)


def test_actor_militia():
    a = extract.extract_actors("the Wagner Group operated in the area")
    assert any("Group" in x for x in a)


def test_actor_dedup():
    a = extract.extract_actors("Forces of A clashed with Forces of A")
    assert a.count("Forces of A") == 1


def test_actor_none():
    assert extract.extract_actors("shelling was reported downtown") == []


# --- country / places --------------------------------------------------------
def test_country_match():
    assert extract.extract_country("fighting in Ukraine intensified") == "Ukraine"


def test_country_longest_wins():
    assert extract.extract_country("clashes in South Sudan") == "South Sudan"


def test_country_none():
    assert extract.extract_country("fighting in the valley") == ""


def test_places_preposition():
    places = extract.extract_places("clashes near Kramatorsk and in Bakhmut")
    assert "Kramatorsk" in places and "Bakhmut" in places


def test_places_skips_months():
    assert "June" not in extract.extract_places("on 12 June forces moved")


def test_places_none():
    assert extract.extract_places("fighting continued") == []


# --- classify ----------------------------------------------------------------
def test_classify_drone():
    assert extract.classify_event_type("an FPV drone attack") == "drone/uas"


def test_classify_battle():
    assert extract.classify_event_type("armed clash between units") == "battle"


def test_classify_protest():
    assert extract.classify_event_type("a demonstration in the square") == "protests"


def test_classify_other():
    assert extract.classify_event_type("a diplomatic meeting") == "other"


# --- full extract ------------------------------------------------------------
def test_extract_full_fields():
    f = extract.extract("At least 6 killed in a drone strike near Kramatorsk, Ukraine "
                        "on 12 June 2026. Forces of Russia struck positions.",
                        source="wire")
    assert f["date"] == "2026-06-12"
    assert f["event_type"] == "drone/uas"
    assert f["country"] == "Ukraine"
    assert f["location"] == "Kramatorsk"
    assert f["fatalities"] == 6
    assert f["actor1"] == "Forces of Russia"
    assert "drone-uas" in f["platforms"]


def test_extract_confidence_high():
    f = extract.extract("6 killed in a drone strike near Kramatorsk, Ukraine on 2026-06-12 "
                        "by Forces of Russia", source="x")
    assert f["confidence"] == "high"


def test_extract_confidence_low():
    f = extract.extract("something happened somewhere", source="x")
    assert f["confidence"] == "low"


def test_extract_default_date_used():
    f = extract.extract("clashes reported", default_date="2026-01-01")
    assert f["date"] == "2026-01-01"


def test_extract_location_not_equal_country():
    f = extract.extract("fighting in Ukraine near Kharkiv")
    assert f["country"] == "Ukraine" and f["location"] != "Ukraine"


def test_extract_notes_truncated():
    f = extract.extract("x " * 400)
    assert len(f["notes"]) <= 280


# --- to_event ----------------------------------------------------------------
def test_to_event_type():
    e = extract.to_event("An FPV drone hit an armored column in Donetsk, Ukraine.")
    assert isinstance(e, ConflictEvent)
    assert e.event_type == "drone/uas"
    assert e.country == "Ukraine"


def test_to_event_tags_platforms():
    e = extract.to_event("a Shahed drone struck the substation")
    assert "shahed-loitering-munition" in e.tags


def test_to_event_confidence_tag():
    e = extract.to_event("6 killed in drone strike near Kramatorsk, Ukraine 2026-06-12 "
                         "by Forces of Russia")
    assert any(t.startswith("extracted:") for t in e.tags)


def test_to_event_wounded_tag():
    e = extract.to_event("3 killed and 9 wounded in shelling in Sudan")
    assert "wounded:9" in e.tags


def test_to_event_source_propagated():
    e = extract.to_event("clashes in Mali", source="reliefweb")
    assert e.source == "reliefweb"


# --- extract_all -------------------------------------------------------------
def test_extract_all_count():
    texts = ["drone strike in Ukraine", "protest in Georgia", "shelling in Gaza"]
    evs = extract.extract_all(texts)
    assert len(evs) == 3
    assert all(isinstance(e, ConflictEvent) for e in evs)


def test_extract_all_skips_blank():
    evs = extract.extract_all(["real report", "", "   ", None])
    assert len(evs) == 1


def test_extract_all_deterministic():
    texts = ["drone strike in Ukraine 2026-06-01", "battle in Sudan 2026-06-02"]
    a = json.dumps([e.to_dict() for e in extract.extract_all(texts)], sort_keys=True)
    b = json.dumps([e.to_dict() for e in extract.extract_all(texts)], sort_keys=True)
    assert a == b


# --- CLI ---------------------------------------------------------------------
def _run(argv, capsys):
    rc = main(argv)
    return rc, capsys.readouterr().out


def test_cli_extract_text(capsys):
    rc, out = _run(["extract", "--text",
                    "6 killed in a drone strike near Kramatorsk, Ukraine on 2026-06-12"],
                   capsys)
    assert rc == 0 and "drone/uas" in out


def test_cli_extract_json(capsys):
    rc, out = _run(["extract", "--text", "battle in Sudan on 2026-06-01",
                    "--format", "json"], capsys)
    assert rc == 0
    data = json.loads(out)
    assert data and data[0]["event_type"] == "battle"


def test_cli_extract_fields(capsys):
    rc, out = _run(["extract", "--text", "3 killed in shelling in Gaza", "--fields"], capsys)
    assert rc == 0
    data = json.loads(out)
    assert data[0]["fatalities"] == 3


def test_cli_extract_from_file(tmp_path, capsys):
    p = tmp_path / "reports.txt"
    p.write_text("drone strike in Ukraine\nprotest in Georgia\n", encoding="utf-8")
    rc, out = _run(["extract", "--from-file", str(p), "--format", "json"], capsys)
    assert rc == 0
    assert len(json.loads(out)) == 2


def test_cli_extract_out_file(tmp_path, capsys):
    p = tmp_path / "ev.json"
    rc, out = _run(["extract", "--text", "battle in Mali 2026-06-03", "--out", str(p)], capsys)
    assert rc == 0 and p.exists()
    assert json.loads(p.read_text(encoding="utf-8"))[0]["event_type"] == "battle"
