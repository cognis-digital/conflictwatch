"""Tests: event normalization, dataset adapters, analysis, lessons KB, CLI, scope."""

from __future__ import annotations

import json
import os

from conflictwatch import TOOL_NAME, TOOL_VERSION, analyze, lessons, scrape
from conflictwatch.events import ConflictEvent, dedupe, normalize
from conflictwatch.sources import parse, parse_acled_csv, parse_gdelt_tsv
from conflictwatch.cli import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACLED = os.path.join(ROOT, "demos", "sample_acled.csv")


def _events():
    with open(ACLED, encoding="utf-8") as fh:
        return parse_acled_csv(fh.read())


# --- model -------------------------------------------------------------------
def test_metadata():
    assert TOOL_NAME == "conflictwatch" and TOOL_VERSION.count(".") == 2


def test_normalize_aliases_and_types():
    e = normalize({"event_date": "2026-06-12", "type": "Drone strike", "side_a": "X",
                   "latitude": "48.9", "longitude": "37.4", "killed": "2", "admin1": "East"},
                  source="t")
    assert e.event_type == "drone/uas" and e.actor1 == "X" and e.region == "East"
    assert e.lat == 48.9 and e.fatalities == 2 and e.date == "2026-06-12"


def test_severity_tiers():
    assert ConflictEvent(fatalities=30).severity == "critical"
    assert ConflictEvent(fatalities=6).severity == "high"
    assert ConflictEvent(fatalities=1).severity == "medium"
    assert ConflictEvent(event_type="drone/uas").severity == "low"
    assert ConflictEvent(event_type="protests").severity == "info"


def test_dedupe_is_stable():
    e = _events()
    assert len(dedupe(e + e)) == len(dedupe(e))


# --- adapters ----------------------------------------------------------------
def test_acled_adapter_and_type_coercion():
    e = _events()
    assert len(e) == 8
    types = {x.event_type for x in e}
    assert "drone/uas" in types and "battle" in types
    assert any(x.event_type == "drone/uas" and x.fatalities == 2 for x in e)


def test_gdelt_tsv_tolerant():
    # minimal 61-col GDELT row; only a few columns populated
    cols = [""] * 61
    cols[1] = "20260612"; cols[6] = "MIL"; cols[16] = "GOV"
    cols[52] = "Vale, East, Borderland"; cols[56] = "48.9"; cols[57] = "37.4"; cols[60] = "http://x"
    ev = parse_gdelt_tsv("\t".join(cols))
    assert len(ev) == 1 and ev[0].country == "Borderland" and ev[0].lat == 48.9


def test_parse_unknown_source_raises():
    try:
        parse("nope", "x"); assert False
    except ValueError:
        pass


# --- analysis ----------------------------------------------------------------
def test_summary_hotspots_actors_trend():
    s = analyze.summary(_events())
    assert s["total_events"] == 8 and s["total_fatalities"] == 28
    assert s["hotspots"][0]["fatalities"] >= s["hotspots"][-1]["fatalities"]
    assert any(a["actor"] == "Forces of A" for a in s["top_actors"])
    assert "by_type" in s and s["by_type"].get("drone/uas", 0) >= 1


def test_timeline_sorted():
    tl = analyze.timeline(_events())
    assert tl == sorted(tl, key=lambda d: d["date"]) and tl[0]["events"] >= 1


# --- lessons -----------------------------------------------------------------
def test_lessons_load_and_query():
    alll = lessons.load()
    assert len(alll) >= 9
    cu = lessons.query(category="counter-uas")
    assert cu and all(l["category"] == "counter-uas" for l in cu)
    assert all("countermeasures" in l for l in alll)


def test_lessons_are_descriptive_not_offensive():
    """Scope guard: the KB must not carry targeting/weapon-build content."""
    blob = json.dumps(lessons.load()).lower()
    for bad in ("kill chain", "how to build a", "fire control", "assassinat"):
        assert bad not in blob


# --- scraping (offline) ------------------------------------------------------
def test_parse_feed_offline():
    rss = ("<rss><channel><item><title>Drone strike near Vale</title>"
           "<link>http://x/1</link><pubDate>2026-06-12</pubDate>"
           "<description>FPV drone hit a vehicle</description></item></channel></rss>")
    items = scrape.parse_feed(rss)
    assert len(items) == 1 and items[0]["title"].startswith("Drone strike")
    ev = scrape.items_to_events(items)[0]
    assert ev.event_type == "drone/uas"


# --- CLI ---------------------------------------------------------------------
def test_cli_ingest_and_analyze(tmp_path, capsys):
    out = str(tmp_path / "e.json")
    assert main(["ingest", "--source", "acled", "--from-file", ACLED, "--out", out]) == 0
    data = json.load(open(out, encoding="utf-8"))
    assert len(data) == 8
    capsys.readouterr()                       # drain ingest's "wrote ..." line
    assert main(["analyze", out, "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["total_events"] == 8


def test_cli_lessons(capsys):
    assert main(["lessons", "--category", "counter-uas", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)
