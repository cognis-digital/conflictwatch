"""Tests for the rolling escalation-index module (conflictwatch.escalation).

Every test is deterministic and offline: events are constructed in-memory or loaded
from the committed fixture (demos/sample_escalation.json). The index is a bounded
0-100 dial blending tempo, intensity (lethality) and geographic spread, each measured
against a theatre's own trailing baseline, with a rising/falling trajectory tag.
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta

import pytest

from conflictwatch import escalation
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


def _series(start_off, end_off, per_day, **kw):
    """Events from start_off..end_off days before END, per_day each day (>=1)."""
    out = []
    for off in range(start_off, end_off - 1, -1):
        d = (END - timedelta(days=off)).isoformat()
        for _ in range(per_day):
            out.append(_ev(d, **kw))
    return out


def _flat(days, per_day, **kw):
    """A perfectly flat series of `days` days ending at END."""
    return _series(days - 1, 0, per_day, **kw)


def _fixture_events():
    with open(FIXTURE, encoding="utf-8") as fh:
        return parse_generic_json(fh.read())


# --------------------------------------------------------------------------- #
# module surface / metadata
# --------------------------------------------------------------------------- #
def test_module_exports():
    for name in ("index_series", "current", "summary"):
        assert hasattr(escalation, name)
    assert escalation.COMPONENTS == ("tempo", "intensity", "spread")


def test_levels_ascending_known_set():
    assert escalation.LEVELS == ("calm", "guarded", "elevated", "high", "severe")


def test_default_weights_sum_to_one():
    assert abs(sum(escalation.DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9
    assert set(escalation.DEFAULT_WEIGHTS) == set(escalation.COMPONENTS)


def test_registered_in_package():
    import conflictwatch
    assert conflictwatch.escalation is escalation
    assert "escalation" in conflictwatch.__all__


# --------------------------------------------------------------------------- #
# empty / degenerate input
# --------------------------------------------------------------------------- #
def test_index_series_empty():
    assert escalation.index_series([]) == []


def test_index_series_all_undated_empty():
    evs = [ConflictEvent(country="X", notes="no date") for _ in range(5)]
    assert escalation.index_series(evs) == []


def test_current_empty_is_calm_zero():
    cur = escalation.current([])
    assert cur["index"] == 0.0 and cur["level"] == "calm"
    assert cur["direction"] == "steady" and cur["points"] == 0
    assert cur["date"] is None and cur["drivers"] == []


def test_summary_empty_safe():
    s = escalation.summary([])
    assert s["points"] == 0 and s["peak"] is None
    assert s["rising_days"] == 0 and s["falling_days"] == 0
    assert s["series"] == []


def test_short_series_no_baseline_empty():
    # only a few days — cannot form a full baseline window -> no scored points
    evs = _flat(5, 2)
    assert escalation.index_series(evs, window=7, baseline_windows=4) == []


# --------------------------------------------------------------------------- #
# structural invariants of index_series
# --------------------------------------------------------------------------- #
def test_series_point_shape():
    evs = _flat(60, 2) + _series(6, 0, 8)  # surge in the last week
    series = escalation.index_series(evs)
    assert series
    keys = {"date", "index", "level", "direction", "delta",
            "tempo", "intensity", "spread", "volume", "fatalities", "locations"}
    for p in series:
        assert keys <= set(p)


def test_series_dates_ascending_unique():
    evs = _flat(60, 3)
    series = escalation.index_series(evs)
    dates = [p["date"] for p in series]
    assert dates == sorted(dates)
    assert len(dates) == len(set(dates))


def test_index_bounded_0_100():
    evs = _flat(40, 1) + _series(6, 0, 40, location="Front", fatalities=9)
    for p in escalation.index_series(evs):
        assert 0.0 <= p["index"] <= 100.0
        for c in escalation.COMPONENTS:
            assert 0.0 <= p[c] <= 100.0


def test_levels_match_index_bands():
    evs = _fixture_events()
    for p in escalation.index_series(evs):
        assert p["level"] == escalation._level_of(p["index"])


def test_directions_are_known_tokens():
    evs = _fixture_events()
    tokens = {"rising", "falling", "steady"}
    for p in escalation.index_series(evs):
        assert p["direction"] in tokens


# --------------------------------------------------------------------------- #
# behavioural: flat vs escalating
# --------------------------------------------------------------------------- #
def test_flat_series_reads_low():
    # a busy but perfectly steady conflict is NOT escalating
    evs = _flat(80, 5, location="Town", fatalities=2)
    cur = escalation.current(evs)
    assert cur["index"] < 20.0
    assert cur["level"] == "calm"


def test_flat_series_never_rising():
    evs = _flat(80, 4, location="Town")
    assert all(p["direction"] != "rising" for p in escalation.index_series(evs))


def test_tempo_surge_lifts_index():
    base = _series(80, 8, 2, location="Town")          # quiet baseline
    surge = _series(6, 0, 20, location="Town")         # 10x tempo surge
    cur = escalation.current(base + surge)
    assert cur["index"] > 25.0
    assert cur["components"]["tempo"] > 50.0
    assert cur["direction"] == "rising"


def test_lethality_surge_lifts_intensity_component():
    base = _series(80, 8, 4, location="Town", fatalities=0)
    surge = _series(6, 0, 4, location="Town", fatalities=10)  # deadly, same tempo
    cur = escalation.current(base + surge)
    assert cur["components"]["intensity"] > 50.0


def test_geo_spread_lifts_spread_component():
    # baseline confined to one town, recent week spreads to many towns, same tempo
    base = _series(80, 8, 6, location="Town")
    surge = []
    for off in range(6, -1, -1):
        d = (END - timedelta(days=off)).isoformat()
        for j in range(6):
            surge.append(_ev(d, location=f"Place{j}"))
    cur = escalation.current(base + surge)
    assert cur["components"]["spread"] > 40.0


def test_tiny_volume_blip_damped():
    # a 0->2 blip on an otherwise dead series must not light up the dial
    base = _flat(80, 0)  # nothing but zero-days is degenerate; seed a couple
    evs = [_ev((END - timedelta(days=40)).isoformat())]
    evs += _series(1, 0, 1, location="Blip")  # 2 events total in recent window
    cur = escalation.current(evs)
    assert cur["index"] < 25.0


# --------------------------------------------------------------------------- #
# trajectory / trend classification
# --------------------------------------------------------------------------- #
def test_rising_trajectory_on_step_up():
    # a quiet baseline that steps sharply up in the final window -> the index a
    # window ago was low, now high -> rising (escalation is accelerating).
    quiet = _series(60, 8, 2, location="Town")
    jump = _series(6, 0, 20, location="Town")
    cur = escalation.current(quiet + jump)
    assert cur["direction"] == "rising"
    assert cur["slope_per_day"] > 0


def test_falling_trajectory_after_peak():
    base = _series(80, 15, 2, location="Town")
    peak = _series(14, 8, 25, location="Town")   # a big surge...
    calm = _series(7, 0, 2, location="Town")     # ...then back to quiet
    series = escalation.index_series(base + peak + calm)
    assert series[-1]["direction"] == "falling"


def test_delta_none_when_no_prior_window():
    evs = _flat(60, 3)
    series = escalation.index_series(evs, window=7)
    # earliest scored points cannot look a full window back
    assert series[0]["delta"] is None
    assert series[0]["direction"] == "steady"


def test_classify_helper():
    assert escalation._classify(None, 5.0) == "steady"
    assert escalation._classify(6.0, 5.0) == "rising"
    assert escalation._classify(-6.0, 5.0) == "falling"
    assert escalation._classify(2.0, 5.0) == "steady"
    assert escalation._classify(-2.0, 5.0) == "steady"


@pytest.mark.parametrize("rise", [1.0, 5.0, 20.0, 60.0])
def test_higher_rise_threshold_never_more_rising(rise):
    evs = _fixture_events()
    strict = escalation.index_series(evs, rise_threshold=60.0)
    loose = escalation.index_series(evs, rise_threshold=rise)
    n_strict = sum(1 for p in strict if p["direction"] == "rising")
    n_loose = sum(1 for p in loose if p["direction"] == "rising")
    assert n_loose >= n_strict


# --------------------------------------------------------------------------- #
# drivers / current snapshot
# --------------------------------------------------------------------------- #
def test_drivers_ranked_by_contribution():
    evs = _series(80, 8, 2, location="Town") + _series(6, 0, 20, location="Town")
    cur = escalation.current(evs)
    contribs = [d["contribution"] for d in cur["drivers"]]
    assert contribs == sorted(contribs, reverse=True)
    assert {d["component"] for d in cur["drivers"]} == set(escalation.COMPONENTS)


def test_driver_contribution_matches_weight_times_score():
    evs = _fixture_events()
    cur = escalation.current(evs)
    for d in cur["drivers"]:
        assert abs(d["contribution"] - round(d["weight"] * d["score"], 1)) < 0.11


def test_current_index_matches_last_series_point():
    evs = _fixture_events()
    series = escalation.index_series(evs)
    cur = escalation.current(evs)
    assert cur["index"] == series[-1]["index"]
    assert cur["date"] == series[-1]["date"]


# --------------------------------------------------------------------------- #
# weights
# --------------------------------------------------------------------------- #
def test_weights_normalized_internally():
    assert escalation._norm_weights({"tempo": 2, "intensity": 2, "spread": 4}) == {
        "tempo": 0.25, "intensity": 0.25, "spread": 0.5}


def test_weights_empty_falls_back_to_default():
    assert escalation._norm_weights(None) == escalation.DEFAULT_WEIGHTS
    assert escalation._norm_weights({}) == escalation.DEFAULT_WEIGHTS
    assert escalation._norm_weights({"tempo": 0, "intensity": 0, "spread": 0}) \
        == escalation.DEFAULT_WEIGHTS


def test_weights_negative_clamped():
    w = escalation._norm_weights({"tempo": -5, "intensity": 1, "spread": 1})
    assert w["tempo"] == 0.0 and abs(w["intensity"] - 0.5) < 1e-9


def test_tempo_only_weights_isolate_component():
    base = _series(80, 8, 4, location="Town", fatalities=0)
    surge = _series(6, 0, 4, location="Town", fatalities=10)  # lethality only
    w = {"tempo": 1.0, "intensity": 0.0, "spread": 0.0}
    cur = escalation.current(base + surge, weights=w)
    # tempo is flat here, so a tempo-only index should stay low despite the deaths
    assert cur["index"] < 20.0


# --------------------------------------------------------------------------- #
# scope filters (country / region)
# --------------------------------------------------------------------------- #
def test_country_filter_scopes_series():
    a = _series(60, 0, 3, country="Aland", location="A")            # flat
    b = (_series(60, 8, 2, country="Bland", location="B")           # quiet baseline
         + _series(6, 0, 30, country="Bland", location="B"))        # then surges
    both = a + b
    cur_a = escalation.current(both, country="Aland")
    cur_b = escalation.current(both, country="Bland")
    assert cur_b["index"] > cur_a["index"]


def test_country_filter_no_match_empty():
    evs = _series(60, 0, 3, country="Aland")
    assert escalation.index_series(evs, country="Nowhere") == []


def test_region_filter_scopes_series():
    a = _series(60, 0, 3, region="North", location="N")
    b = (_series(60, 8, 2, region="South", location="S")
         + _series(6, 0, 30, region="South", location="S"))
    cur = escalation.current(a + b, region="South")
    assert cur["index"] > 0.0


# --------------------------------------------------------------------------- #
# as_of replay
# --------------------------------------------------------------------------- #
def test_as_of_truncates_series():
    evs = _fixture_events()
    full = escalation.index_series(evs)
    early = escalation.index_series(evs, as_of="2026-06-10")
    assert early
    assert all(p["date"] <= "2026-06-10" for p in early)
    assert len(early) < len(full)


def test_as_of_before_data_empty():
    evs = _fixture_events()
    assert escalation.index_series(evs, as_of="2000-01-01") == []


def test_as_of_replays_pre_surge_calm():
    # evaluated before the surge, the fixture theatre should not read escalated-high
    evs = _fixture_events()
    cur = escalation.current(evs, as_of="2026-05-30")
    assert cur["index"] < escalation.current(evs)["index"]


# --------------------------------------------------------------------------- #
# saturation curve properties
# --------------------------------------------------------------------------- #
def test_saturate_zero_at_or_below_baseline():
    assert escalation._saturate(1.0) == 0.0
    assert escalation._saturate(0.5) == 0.0
    assert escalation._saturate(0.0) == 0.0


def test_saturate_monotone_and_bounded():
    prev = -1.0
    for r in [1.0, 1.5, 2.0, 3.0, 5.0, 10.0, 100.0, 1e6]:
        v = escalation._saturate(r)
        assert 0.0 <= v <= 100.0
        assert v >= prev
        prev = v


@pytest.mark.parametrize("ratio", [1.0, 1.25, 1.5, 2.0, 2.5, 4.0, 8.0, 25.0])
def test_saturate_range_sweep(ratio):
    v = escalation._saturate(ratio)
    assert 0.0 <= v < 100.0
    if ratio > 1.0:
        assert v > 0.0


# --------------------------------------------------------------------------- #
# level bands
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("idx,level", [
    (0.0, "calm"), (19.9, "calm"), (20.0, "guarded"), (39.9, "guarded"),
    (40.0, "elevated"), (59.9, "elevated"), (60.0, "high"), (79.9, "high"),
    (80.0, "severe"), (100.0, "severe"),
])
def test_level_bands(idx, level):
    assert escalation._level_of(idx) == level


# --------------------------------------------------------------------------- #
# parametrized property sweeps over synthetic scenarios
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("window", [3, 5, 7, 14])
@pytest.mark.parametrize("baseline_windows", [2, 3, 4])
def test_all_indices_bounded_across_params(window, baseline_windows):
    evs = _flat(120, 3, location="Town") + _series(6, 0, 15, location="Town", fatalities=4)
    series = escalation.index_series(evs, window=window,
                                     baseline_windows=baseline_windows)
    assert series  # enough history for these params
    for p in series:
        assert 0.0 <= p["index"] <= 100.0
        assert p["level"] in escalation.LEVELS


@pytest.mark.parametrize("mult", [2, 4, 8, 16])
def test_bigger_surge_never_lowers_index(mult):
    base = _series(80, 8, 2, location="Town")
    small = escalation.current(base + _series(6, 0, 4, location="Town"))
    big = escalation.current(base + _series(6, 0, 4 * mult, location="Town"))
    assert big["index"] >= small["index"]


@pytest.mark.parametrize("per_day", [1, 2, 5, 10])
def test_flat_series_any_level_stays_calm(per_day):
    evs = _flat(90, per_day, location="Town", fatalities=1)
    assert escalation.current(evs)["index"] < 20.0


@pytest.mark.parametrize("window", [3, 7, 14])
def test_window_shifts_do_not_crash_and_bounded(window):
    evs = _fixture_events()
    cur = escalation.current(evs, window=window)
    assert 0.0 <= cur["index"] <= 100.0


# --------------------------------------------------------------------------- #
# summary roll-up
# --------------------------------------------------------------------------- #
def test_summary_structure():
    s = escalation.summary(_fixture_events())
    assert set(s) >= {"current", "points", "peak", "rising_days",
                      "falling_days", "series"}
    assert s["points"] == len(s["series"])


def test_summary_peak_is_series_max():
    s = escalation.summary(_fixture_events())
    assert s["peak"]["index"] == max(p["index"] for p in s["series"])


def test_summary_rising_falling_counts_consistent():
    s = escalation.summary(_fixture_events())
    rising = sum(1 for p in s["series"] if p["direction"] == "rising")
    falling = sum(1 for p in s["series"] if p["direction"] == "falling")
    assert s["rising_days"] == rising and s["falling_days"] == falling


# --------------------------------------------------------------------------- #
# determinism
# --------------------------------------------------------------------------- #
def test_deterministic_under_reordering():
    evs = _fixture_events()
    a = json.dumps(escalation.summary(evs), sort_keys=True)
    b = json.dumps(escalation.summary(list(reversed(evs))), sort_keys=True)
    assert a == b


def test_deterministic_repeated_calls():
    evs = _flat(60, 3, location="Town") + _series(6, 0, 12, location="Town")
    a = escalation.index_series(evs)
    b = escalation.index_series(evs)
    assert a == b


def test_fixture_scenario_escalates_by_end():
    # the committed fixture is a quiet baseline followed by a surge; the final
    # read must be escalated and rising.
    cur = escalation.current(_fixture_events())
    assert cur["index"] >= 40.0
    assert cur["direction"] == "rising"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _run(argv, capsys):
    rc = main(argv)
    return rc, capsys.readouterr().out


def test_cli_escalation_table(capsys):
    rc, out = _run(["escalation", FIXTURE], capsys)
    assert rc == 0
    assert "escalation index" in out.lower()
    assert "drivers" in out.lower()


def test_cli_escalation_json(capsys):
    rc, out = _run(["escalation", FIXTURE, "--format", "json"], capsys)
    assert rc == 0
    data = json.loads(out)
    assert "current" in data and "series" in data
    assert 0.0 <= data["current"]["index"] <= 100.0


def test_cli_escalation_as_of(capsys):
    rc, out = _run(["escalation", FIXTURE, "--as-of", "2026-06-10",
                    "--format", "json"], capsys)
    assert rc == 0
    data = json.loads(out)
    assert all(p["date"] <= "2026-06-10" for p in data["series"])


def test_cli_escalation_window(capsys):
    rc, out = _run(["escalation", FIXTURE, "--window", "5",
                    "--baseline-windows", "3", "--format", "json"], capsys)
    assert rc == 0
    assert "current" in json.loads(out)
