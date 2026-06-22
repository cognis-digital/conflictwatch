"""Tests for the escalation early-warning module (conflictwatch.watch).

Every test is deterministic and offline: events are constructed in-memory or loaded
from a committed fixture (demos/sample_escalation.json). The fixture encodes a known
scenario — a quiet 28-day baseline in 'Borderland' followed by a 7-day surge (a new
location 'Newcross', a new actor 'Volunteer Brigade', and rising lethality) plus a
permanently-quiet 'Calmland' that must never raise a high-severity alert.
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta

import pytest

from conflictwatch import watch
from conflictwatch.events import ConflictEvent
from conflictwatch.sources import parse_generic_json
from conflictwatch.cli import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "demos", "sample_escalation.json")
END = date(2026, 6, 20)


# --------------------------------------------------------------------------- #
# builders
# --------------------------------------------------------------------------- #
def _ev(d, **kw):
    kw.setdefault("country", "Testland")
    kw.setdefault("event_type", "battle")
    return ConflictEvent(date=d, **kw)


def _series(country, start_off, end_off, per_day, **kw):
    """Events from start_off..end_off days before END, per_day each day."""
    out = []
    for off in range(start_off, end_off - 1, -1):
        d = (END - timedelta(days=off)).isoformat()
        for _ in range(per_day):
            out.append(_ev(d, country=country, **kw))
    return out


def _fixture_events():
    with open(FIXTURE, encoding="utf-8") as fh:
        return parse_generic_json(fh.read())


# --------------------------------------------------------------------------- #
# module surface / metadata
# --------------------------------------------------------------------------- #
def test_module_exports():
    assert hasattr(watch, "detect") and hasattr(watch, "summary")
    assert "spike" in watch.DETECTORS
    assert watch.SEVERITIES[0] == "info" and watch.SEVERITIES[-1] == "critical"


def test_detectors_are_unique():
    assert len(set(watch.DETECTORS)) == len(watch.DETECTORS)


def test_severities_ascending_known_set():
    assert watch.SEVERITIES == ("info", "low", "medium", "high", "critical")


# --------------------------------------------------------------------------- #
# empty / degenerate input
# --------------------------------------------------------------------------- #
def test_detect_empty_returns_empty():
    assert watch.detect([]) == []


def test_detect_all_undated_returns_empty():
    evs = [ConflictEvent(country="X", notes="no date") for _ in range(5)]
    assert watch.detect(evs) == []


def test_detect_single_event_no_crash():
    assert watch.detect([_ev("2026-06-20")]) == []


def test_summary_empty():
    s = watch.summary([])
    assert s["total_alerts"] == 0
    assert s["highest"] == "info"
    assert s["top_alert"] is None
    assert s["alerts"] == []


def test_bad_scope_raises():
    with pytest.raises(ValueError):
        watch.detect([_ev("2026-06-20")], scope="planet")


# --------------------------------------------------------------------------- #
# spike detector
# --------------------------------------------------------------------------- #
def test_spike_fires_on_surge():
    evs = _series("Surgeland", 35, 8, 1) + _series("Surgeland", 6, 0, 8)
    alerts = watch.detect(evs, scope="country")
    spikes = [a for a in alerts if a["detector"] == "spike"]
    assert spikes, "expected a spike alert"
    assert spikes[0]["severity"] in ("high", "critical")
    assert spikes[0]["evidence"]["recent_events"] >= 40


def test_spike_quiet_series_no_alert():
    evs = _series("Calm", 35, 0, 1)  # perfectly steady
    spikes = [a for a in watch.detect(evs, scope="country")
              if a["detector"] == "spike"]
    assert spikes == []


def test_spike_score_scales_with_magnitude():
    small = _series("A", 35, 8, 1) + _series("A", 6, 0, 3)
    big = _series("B", 35, 8, 1) + _series("B", 6, 0, 12)
    a_small = next(a for a in watch.detect(small) if a["detector"] == "spike")
    a_big = next(a for a in watch.detect(big) if a["detector"] == "spike")
    assert a_big["score"] > a_small["score"]


def test_spike_evidence_has_window():
    evs = _series("A", 35, 8, 1) + _series("A", 6, 0, 8)
    a = next(x for x in watch.detect(evs, window=7) if x["detector"] == "spike")
    assert a["evidence"]["window_days"] == 7


# --------------------------------------------------------------------------- #
# sustained-trend detector
# --------------------------------------------------------------------------- #
def test_sustained_trend_fires():
    # prior window 4/day, recent window 9/day -> ~2.25x
    evs = (_series("T", 35, 15, 1)
           + _series("T", 13, 7, 4)   # prior window
           + _series("T", 6, 0, 9))   # recent window
    trends = [a for a in watch.detect(evs) if a["detector"] == "sustained-trend"]
    assert trends
    assert trends[0]["evidence"]["ratio"] >= 1.5


def test_sustained_trend_flat_no_alert():
    evs = _series("T", 35, 0, 3)
    trends = [a for a in watch.detect(evs) if a["detector"] == "sustained-trend"]
    assert trends == []


def test_sustained_trend_requires_floor():
    # prior=2 events, recent=3 events: above the 1.5x ratio but below the
    # absolute recent floor of 4 -> must not fire
    evs = (_ev((END - timedelta(days=12)).isoformat(), country="T"),
           _ev((END - timedelta(days=10)).isoformat(), country="T"),
           _ev((END - timedelta(days=5)).isoformat(), country="T"),
           _ev((END - timedelta(days=3)).isoformat(), country="T"),
           _ev((END - timedelta(days=1)).isoformat(), country="T"))
    trends = [a for a in watch.detect(list(evs)) if a["detector"] == "sustained-trend"]
    assert trends == []


# --------------------------------------------------------------------------- #
# new-actor detector
# --------------------------------------------------------------------------- #
def test_new_actor_detected():
    base = _series("N", 35, 8, 2, actor1="Old Guard", actor2="Old Foe")
    recent = _series("N", 6, 0, 2, actor1="Old Guard", actor2="Fresh Militia")
    alerts = watch.detect(base + recent, scope="country")
    na = [a for a in alerts if a["detector"] == "new-actor"]
    assert na
    assert "Fresh Militia" in na[0]["evidence"]["new_actors"]
    assert "Old Guard" not in na[0]["evidence"]["new_actors"]


def test_new_actor_none_when_same_actors():
    base = _series("N", 35, 8, 2, actor1="Same", actor2="Other")
    recent = _series("N", 6, 0, 2, actor1="Same", actor2="Other")
    na = [a for a in watch.detect(base + recent) if a["detector"] == "new-actor"]
    assert na == []


def test_new_actor_ignores_blank_actors():
    base = _series("N", 35, 8, 2, actor1="Known")
    recent = _series("N", 6, 0, 2, actor1="Known", actor2="")
    na = [a for a in watch.detect(base + recent) if a["detector"] == "new-actor"]
    assert na == []


# --------------------------------------------------------------------------- #
# geo-spread detector
# --------------------------------------------------------------------------- #
def test_geo_spread_fires_when_front_widens():
    # prior window: one location; recent window: five locations
    base = _series("G", 35, 15, 1, location="Town0")
    prior = _series("G", 13, 7, 1, location="Town1")
    recent = []
    for i, loc in enumerate(["Loc1", "Loc2", "Loc3", "Loc4", "Loc5"]):
        recent += _series("G", 6, 0, 1, location=loc)
    alerts = watch.detect(base + prior + recent, scope="country")
    gs = [a for a in alerts if a["detector"] == "geo-spread"]
    assert gs
    assert gs[0]["evidence"]["recent_locations"] >= 5


def test_geo_spread_none_when_concentrated():
    evs = _series("G", 13, 0, 3, location="OneTown")
    gs = [a for a in watch.detect(evs) if a["detector"] == "geo-spread"]
    assert gs == []


# --------------------------------------------------------------------------- #
# lethality-shift detector
# --------------------------------------------------------------------------- #
def test_lethality_shift_fires():
    base = _series("L", 35, 8, 2, fatalities=0)
    recent = _series("L", 6, 0, 2, fatalities=4)
    ls = [a for a in watch.detect(base + recent) if a["detector"] == "lethality-shift"]
    assert ls
    assert ls[0]["evidence"]["recent_lethality"] >= 1.0


def test_lethality_shift_none_when_bloodless():
    evs = _series("L", 35, 0, 2, fatalities=0)
    ls = [a for a in watch.detect(evs) if a["detector"] == "lethality-shift"]
    assert ls == []


def test_lethality_shift_none_when_already_lethal():
    # baseline already 4 fatalities/event -> recent 4 is not a *shift*
    base = _series("L", 35, 8, 2, fatalities=4)
    recent = _series("L", 6, 0, 2, fatalities=4)
    ls = [a for a in watch.detect(base + recent) if a["detector"] == "lethality-shift"]
    assert ls == []


# --------------------------------------------------------------------------- #
# new-hotspot detector
# --------------------------------------------------------------------------- #
def test_new_hotspot_fires():
    base = _series("H", 35, 8, 1, location="OldTown")
    recent = _series("H", 6, 0, 6, location="BrandNew")
    hs = [a for a in watch.detect(base + recent) if a["detector"] == "new-hotspot"]
    assert hs
    assert any(a["evidence"]["location"] == "BrandNew" for a in hs)


def test_new_hotspot_none_for_established_location():
    evs = _series("H", 35, 0, 4, location="Established")
    hs = [a for a in watch.detect(evs) if a["detector"] == "new-hotspot"]
    assert hs == []


# --------------------------------------------------------------------------- #
# severity + volume capping
# --------------------------------------------------------------------------- #
def test_volume_cap_prevents_tiny_critical():
    # 0->2 on a near-empty series: even a huge z must be capped at 'low'
    evs = [_ev((END - timedelta(days=2)).isoformat(), country="Tiny"),
           _ev((END - timedelta(days=1)).isoformat(), country="Tiny")]
    alerts = watch.detect(evs, scope="country")
    for a in alerts:
        assert a["severity"] in ("info", "low")


def test_high_volume_can_reach_critical():
    evs = _series("Big", 35, 8, 1) + _series("Big", 6, 0, 10)
    alerts = watch.detect(evs, scope="country")
    assert any(a["severity"] == "critical" for a in alerts)


def test_min_severity_filter():
    evs = _fixture_events()
    high_only = watch.detect(evs, min_severity="high")
    assert high_only
    assert all(watch.SEVERITIES.index(a["severity"])
               >= watch.SEVERITIES.index("high") for a in high_only)


def test_alerts_sorted_by_severity_desc():
    evs = _fixture_events()
    alerts = watch.detect(evs)
    idxs = [watch.SEVERITIES.index(a["severity"]) for a in alerts]
    assert idxs == sorted(idxs, reverse=True)


# --------------------------------------------------------------------------- #
# scope behavior
# --------------------------------------------------------------------------- #
def test_scope_country_groups():
    evs = _fixture_events()
    alerts = watch.detect(evs, scope="country")
    scopes = {a["scope"] for a in alerts}
    assert "Borderland" in scopes


def test_scope_global_collapses():
    evs = _fixture_events()
    alerts = watch.detect(evs, scope="global")
    assert all(a["scope"] == "(all)" for a in alerts)


def test_scope_region_format():
    evs = _series("R", 35, 8, 1, region="Prov") + _series("R", 6, 0, 8, region="Prov")
    alerts = watch.detect(evs, scope="region")
    assert any("/" in a["scope"] for a in alerts)


def test_scope_location():
    evs = _series("L", 35, 8, 1, location="Spot") + _series("L", 6, 0, 8, location="Spot")
    alerts = watch.detect(evs, scope="location")
    assert any(a["scope"] == "Spot" for a in alerts)


# --------------------------------------------------------------------------- #
# as-of replay (time travel)
# --------------------------------------------------------------------------- #
def test_as_of_before_surge_is_quiet():
    evs = _fixture_events()
    alerts = watch.detect(evs, scope="country", as_of="2026-06-10",
                          min_severity="medium")
    assert alerts == []


def test_as_of_at_end_sees_surge():
    evs = _fixture_events()
    alerts = watch.detect(evs, scope="country", as_of="2026-06-20")
    assert any(a["severity"] == "critical" for a in alerts)


def test_as_of_invalid_falls_back_to_latest():
    evs = _fixture_events()
    a1 = watch.detect(evs, as_of="not-a-date")
    a2 = watch.detect(evs)  # defaults to latest
    assert len(a1) == len(a2)


# --------------------------------------------------------------------------- #
# window / baseline parameters
# --------------------------------------------------------------------------- #
def test_window_zero_coerced_safe():
    evs = _fixture_events()
    # window 0 is coerced to >=1; must not raise
    watch.detect(evs, window=0)


def test_larger_baseline_more_stable():
    evs = _series("S", 60, 8, 1) + _series("S", 6, 0, 8)
    a_small = watch.detect(evs, baseline_windows=2)
    a_big = watch.detect(evs, baseline_windows=6)
    # both should still flag the spike
    assert any(x["detector"] == "spike" for x in a_small)
    assert any(x["detector"] == "spike" for x in a_big)


# --------------------------------------------------------------------------- #
# summary roll-up
# --------------------------------------------------------------------------- #
def test_summary_structure():
    s = watch.summary(_fixture_events(), scope="country")
    assert set(s) >= {"total_alerts", "by_severity", "by_detector",
                      "highest", "top_alert", "alerts"}
    assert s["total_alerts"] == len(s["alerts"])
    assert s["highest"] == "critical"


def test_summary_top_alert_is_highest():
    s = watch.summary(_fixture_events(), scope="country")
    assert s["top_alert"]["severity"] == s["highest"]


def test_summary_by_detector_counts():
    s = watch.summary(_fixture_events(), scope="country")
    assert sum(s["by_detector"].values()) == s["total_alerts"]


# --------------------------------------------------------------------------- #
# determinism
# --------------------------------------------------------------------------- #
def test_deterministic_repeated_runs():
    evs = _fixture_events()
    a1 = json.dumps(watch.detect(evs, scope="country"), sort_keys=True)
    a2 = json.dumps(watch.detect(evs, scope="country"), sort_keys=True)
    assert a1 == a2


def test_input_order_independent():
    evs = _fixture_events()
    a1 = watch.detect(evs, scope="country")
    a2 = watch.detect(list(reversed(evs)), scope="country")
    key = lambda a: (a["detector"], a["scope"], a["severity"])
    assert sorted(map(key, a1)) == sorted(map(key, a2))


# --------------------------------------------------------------------------- #
# scope isolation: a quiet country must not inherit a noisy one's alerts
# --------------------------------------------------------------------------- #
def test_quiet_country_not_high_severity():
    evs = _fixture_events()
    alerts = watch.detect(evs, scope="country")
    for a in alerts:
        if a["scope"] == "Calmland":
            assert a["severity"] in ("info", "low")


# --------------------------------------------------------------------------- #
# CLI integration
# --------------------------------------------------------------------------- #
def _run(argv, capsys):
    rc = main(argv)
    return rc, capsys.readouterr().out


def test_cli_watch_table(capsys):
    rc, out = _run(["watch", FIXTURE], capsys)
    assert rc == 0
    assert "early-warning" in out
    assert "Borderland" in out
    assert "spike" in out


def test_cli_watch_json(capsys):
    rc, out = _run(["watch", FIXTURE, "--format", "json"], capsys)
    assert rc == 0
    data = json.loads(out)
    assert data["highest"] == "critical"
    assert data["total_alerts"] >= 1


def test_cli_watch_min_severity(capsys):
    rc, out = _run(["watch", FIXTURE, "--min-severity", "high", "--format", "json"],
                   capsys)
    data = json.loads(out)
    assert all(watch.SEVERITIES.index(a["severity"])
               >= watch.SEVERITIES.index("high") for a in data["alerts"])


def test_cli_watch_detector_filter(capsys):
    rc, out = _run(["watch", FIXTURE, "--detector", "spike", "--format", "json"],
                   capsys)
    data = json.loads(out)
    assert all(a["detector"] == "spike" for a in data["alerts"])


def test_cli_watch_scope_global(capsys):
    rc, out = _run(["watch", FIXTURE, "--scope", "global", "--format", "json"],
                   capsys)
    data = json.loads(out)
    assert all(a["scope"] == "(all)" for a in data["alerts"])


def test_cli_watch_as_of_quiet(capsys):
    rc, out = _run(["watch", FIXTURE, "--as-of", "2026-06-10",
                    "--min-severity", "medium"], capsys)
    assert rc == 0
    assert "no escalation" in out


def test_cli_watch_rejects_bad_scope(capsys):
    with pytest.raises(SystemExit):
        _run(["watch", FIXTURE, "--scope", "galaxy"], capsys)


def test_cli_watch_rejects_bad_detector(capsys):
    with pytest.raises(SystemExit):
        _run(["watch", FIXTURE, "--detector", "telepathy"], capsys)
