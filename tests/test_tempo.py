"""Tests for per-region event-tempo forecasting (conflictwatch.tempo).

Every test is deterministic and offline: events are constructed in-memory (and, for the
CLI tests, written to a temp JSON file). The module builds a dense daily event-count
series per region, reads the recent pace / momentum / trend slope, and projects near-term
event volume forward with a rising/steady/falling class. It is descriptive early-warning
only — no targeting.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from conflictwatch import tempo
from conflictwatch.events import ConflictEvent
from conflictwatch.cli import main

END = date(2026, 6, 20)


# --------------------------------------------------------------------------- #
# builders
# --------------------------------------------------------------------------- #
def _ev(d, **kw):
    kw.setdefault("country", "Testland")
    kw.setdefault("region", "North")
    kw.setdefault("event_type", "battle")
    return ConflictEvent(date=d, **kw)


def _span(start_off, end_off, per_day, **kw):
    """Events from start_off..end_off days before END, per_day each day."""
    out = []
    for off in range(start_off, end_off - 1, -1):
        d = (END - timedelta(days=off)).isoformat()
        for _ in range(per_day):
            out.append(_ev(d, **kw))
    return out


def _flat(days, per_day, **kw):
    """A perfectly flat series of `days` days ending at END."""
    return _span(days - 1, 0, per_day, **kw)


def _surge(baseline_days=60, base_pd=2, surge_days=7, surge_pd=15, **kw):
    """Quiet baseline then a surge in the final `surge_days` — a rising region."""
    return (_span(baseline_days - 1, surge_days, base_pd, **kw)
            + _span(surge_days - 1, 0, surge_pd, **kw))


def _two_region():
    """North surges, South stays flat — a classic early-warning contrast."""
    return _surge(region="North") + _flat(60, 3, region="South")


# --------------------------------------------------------------------------- #
# module surface / metadata
# --------------------------------------------------------------------------- #
def test_module_exports():
    for name in ("forecast", "region_tempo", "regions", "summary"):
        assert hasattr(tempo, name)


def test_trend_classes_known_set():
    assert set(tempo.TREND_CLASSES) == {"rising", "steady", "falling"}


def test_group_by_known_set():
    assert tempo.GROUP_BY == ("region", "country", "location")


def test_defaults_are_sane():
    assert tempo.DEFAULT_WINDOW >= 1
    assert tempo.DEFAULT_FIT_DAYS >= tempo.DEFAULT_WINDOW
    assert tempo.DEFAULT_HORIZON >= 1
    assert tempo.DEFAULT_MIN_DAYS >= 2
    assert tempo.DEFAULT_RISE > 0


def test_registered_in_package():
    import conflictwatch
    assert conflictwatch.tempo is tempo
    assert "tempo" in conflictwatch.__all__


# --------------------------------------------------------------------------- #
# empty / degenerate input
# --------------------------------------------------------------------------- #
def test_forecast_empty():
    assert tempo.forecast([]) == []


def test_regions_empty():
    assert tempo.regions([]) == []


def test_summary_empty_safe():
    s = tempo.summary([])
    assert s["regions"] == 0
    assert s["rising"] == 0 and s["steady"] == 0 and s["falling"] == 0
    assert s["projected_total"] == 0.0
    assert s["top_rising"] == [] and s["board"] == []


def test_all_undated_empty():
    evs = [ConflictEvent(region="North", notes="no date") for _ in range(5)]
    assert tempo.forecast(evs) == []


def test_no_group_key_skipped():
    # events with neither region nor country nor location produce no board rows
    evs = [ConflictEvent(date=(END - timedelta(days=i)).isoformat()) for i in range(10)]
    assert tempo.forecast(evs) == []


def test_region_tempo_missing_region_none():
    assert tempo.region_tempo(_flat(30, 2, region="North"), "Nowhere") is None


def test_too_short_series_excluded():
    # a region with fewer than min_days of history is not forecastable
    evs = _span(1, 0, 2, region="Blip")  # only 2 days
    assert tempo.forecast(evs, min_days=3) == []
    assert tempo.region_tempo(evs, "Blip", min_days=3) is None


# --------------------------------------------------------------------------- #
# structural invariants
# --------------------------------------------------------------------------- #
ROW_KEYS = {"region", "days", "fit_days", "recent_rate", "prior_rate", "momentum",
            "slope_per_day", "trend", "recent_total", "horizon", "projected_total",
            "projection"}


def test_row_shape():
    for r in tempo.forecast(_two_region()):
        assert ROW_KEYS <= set(r)
        for p in r["projection"]:
            assert {"date", "value", "lo", "hi"} <= set(p)


def test_region_tempo_row_shape():
    r = tempo.region_tempo(_surge(region="North"), "North")
    assert r is not None
    assert ROW_KEYS <= set(r)
    assert r["region"] == "North"


def test_projection_length_matches_horizon():
    for h in (1, 3, 7, 14):
        r = tempo.region_tempo(_surge(region="North"), "North", horizon=h)
        assert len(r["projection"]) == h
        assert r["horizon"] == h


def test_projection_dates_are_future_and_ascending():
    r = tempo.region_tempo(_surge(region="North"), "North", horizon=7)
    dates = [p["date"] for p in r["projection"]]
    assert dates == sorted(dates)
    assert dates[0] == (END + timedelta(days=1)).isoformat()
    assert dates[-1] == (END + timedelta(days=7)).isoformat()


def test_trend_tokens_valid():
    for r in tempo.forecast(_two_region()):
        assert r["trend"] in tempo.TREND_CLASSES


def test_projection_values_nonnegative():
    # a steeply falling region must never project negative counts
    evs = _span(40, 8, 20, region="North") + _span(7, 0, 1, region="North")
    for r in tempo.forecast(evs, horizon=30):
        for p in r["projection"]:
            assert p["value"] >= 0.0
            assert p["lo"] >= 0.0
            assert p["lo"] <= p["value"] <= p["hi"]


def test_projected_total_matches_projection_sum():
    for r in tempo.forecast(_two_region()):
        s = round(sum(p["value"] for p in r["projection"]), 1)
        # projected_total sums exact (unrounded) daily values; agree within rounding
        assert abs(r["projected_total"] - s) <= 0.2 * len(r["projection"]) + 0.1


def test_board_sorted_by_projected_total_desc():
    board = tempo.forecast(_two_region())
    totals = [r["projected_total"] for r in board]
    assert totals == sorted(totals, reverse=True)


def test_regions_are_sorted_unique():
    evs = _flat(30, 2, region="North") + _flat(30, 2, region="South") \
        + _flat(30, 2, region="East")
    regs = tempo.regions(evs)
    assert regs == sorted(regs)
    assert len(regs) == len(set(regs)) == 3


# --------------------------------------------------------------------------- #
# behavioural: rising / falling / steady
# --------------------------------------------------------------------------- #
def test_surge_reads_rising():
    r = tempo.region_tempo(_surge(region="North"), "North")
    assert r["trend"] == "rising"
    assert r["slope_per_day"] > 0
    assert r["momentum"] > 0


def test_flat_reads_steady():
    r = tempo.region_tempo(_flat(60, 4, region="North"), "North")
    assert r["trend"] == "steady"
    assert abs(r["slope_per_day"]) < tempo.DEFAULT_RISE
    assert r["momentum"] == 0.0


def test_decline_reads_falling():
    # busy early, quiet late
    evs = _span(40, 8, 18, region="North") + _span(7, 0, 1, region="North")
    r = tempo.region_tempo(evs, "North")
    assert r["trend"] == "falling"
    assert r["slope_per_day"] < 0
    assert r["momentum"] < 0


def test_rising_region_projects_more_than_flat():
    board = {r["region"]: r for r in tempo.forecast(_two_region())}
    assert board["North"]["projected_total"] > board["South"]["projected_total"]


def test_recent_rate_reflects_window():
    # last 7 days average 10/day
    r = tempo.region_tempo(_flat(30, 10, region="North"), "North", window=7)
    assert r["recent_rate"] == 10.0
    assert r["recent_total"] == 70


def test_prior_rate_none_when_insufficient_history():
    # only one window's worth of days -> no prior window to compare
    r = tempo.region_tempo(_flat(7, 3, region="North"), "North", window=7, min_days=3)
    assert r is not None
    assert r["prior_rate"] is None
    assert r["momentum"] is None


def test_momentum_is_recent_minus_prior():
    evs = _span(13, 7, 2, region="North") + _span(6, 0, 9, region="North")
    r = tempo.region_tempo(evs, "North", window=7)
    assert r["prior_rate"] == 2.0
    assert r["recent_rate"] == 9.0
    assert r["momentum"] == 7.0


# --------------------------------------------------------------------------- #
# grouping lens (by region / country / location)
# --------------------------------------------------------------------------- #
def test_group_by_country():
    evs = _flat(30, 2, region="North", country="Aland") \
        + _flat(30, 3, region="South", country="Bland")
    board = tempo.forecast(evs, by="country")
    keys = {r["region"] for r in board}
    assert keys == {"Aland", "Bland"}


def test_group_by_location():
    evs = _flat(30, 2, location="TownA") + _flat(30, 2, location="TownB")
    board = tempo.forecast(evs, by="location")
    assert {r["region"] for r in board} == {"TownA", "TownB"}


def test_group_by_region_default():
    board = tempo.forecast(_two_region())
    assert {r["region"] for r in board} == {"North", "South"}


# --------------------------------------------------------------------------- #
# country filter
# --------------------------------------------------------------------------- #
def test_country_filter_scopes_board():
    evs = _flat(30, 2, region="North", country="Aland") \
        + _surge(region="South", country="Bland")
    board = tempo.forecast(evs, country="Bland")
    assert {r["region"] for r in board} == {"South"}


def test_country_filter_no_match_empty():
    assert tempo.forecast(_flat(30, 2, country="Aland"), country="Nowhere") == []


# --------------------------------------------------------------------------- #
# as_of replay
# --------------------------------------------------------------------------- #
def test_as_of_projects_from_past_day():
    r = tempo.region_tempo(_surge(region="North"), "North",
                           as_of="2026-06-10", horizon=3)
    assert r["projection"][0]["date"] == "2026-06-11"


def test_as_of_before_data_empty():
    assert tempo.forecast(_surge(region="North"), as_of="2000-01-01") == []


def test_as_of_replays_pre_surge_calm():
    # evaluated before the surge, North should not yet read rising-hot
    evs = _surge(region="North")
    pre = tempo.region_tempo(evs, "North", as_of="2026-05-25")
    post = tempo.region_tempo(evs, "North")
    assert post["slope_per_day"] >= pre["slope_per_day"]


# --------------------------------------------------------------------------- #
# summary roll-up
# --------------------------------------------------------------------------- #
def test_summary_structure():
    s = tempo.summary(_two_region())
    assert set(s) >= {"as_of", "horizon", "regions", "rising", "steady", "falling",
                      "projected_total", "top_rising", "board"}
    assert s["regions"] == len(s["board"])


def test_summary_class_counts_consistent():
    s = tempo.summary(_two_region())
    rising = sum(1 for r in s["board"] if r["trend"] == "rising")
    steady = sum(1 for r in s["board"] if r["trend"] == "steady")
    falling = sum(1 for r in s["board"] if r["trend"] == "falling")
    assert (s["rising"], s["steady"], s["falling"]) == (rising, steady, falling)
    assert s["rising"] + s["steady"] + s["falling"] == s["regions"]


def test_summary_projected_total_is_board_sum():
    s = tempo.summary(_two_region())
    assert s["projected_total"] == round(sum(r["projected_total"]
                                             for r in s["board"]), 1)


def test_summary_top_rising_only_rising():
    s = tempo.summary(_two_region())
    rising_regions = {r["region"] for r in s["board"] if r["trend"] == "rising"}
    assert all(t["region"] in rising_regions for t in s["top_rising"])


def test_summary_top_rising_sorted_by_slope():
    evs = (_surge(region="North", surge_pd=20)
           + _surge(region="South", surge_pd=10)
           + _flat(60, 3, region="East"))
    s = tempo.summary(evs)
    slopes = [t["slope_per_day"] for t in s["top_rising"]]
    assert slopes == sorted(slopes, reverse=True)


def test_summary_top_respects_limit():
    evs = []
    for i in range(6):
        evs += _surge(region=f"R{i}", surge_pd=10 + i)
    s = tempo.summary(evs, top=3)
    assert len(s["top_rising"]) <= 3


def test_summary_top_zero():
    s = tempo.summary(_two_region(), top=0)
    assert s["top_rising"] == []


# --------------------------------------------------------------------------- #
# helper: _lstsq
# --------------------------------------------------------------------------- #
def test_lstsq_perfect_line():
    slope, intercept, resid = tempo._lstsq([0.0, 1.0, 2.0, 3.0, 4.0])
    assert abs(slope - 1.0) < 1e-9
    assert abs(intercept - 0.0) < 1e-9
    assert resid < 1e-9


def test_lstsq_flat_zero_slope():
    slope, intercept, resid = tempo._lstsq([5.0, 5.0, 5.0, 5.0])
    assert slope == 0.0
    assert abs(intercept - 5.0) < 1e-9
    assert resid < 1e-9


def test_lstsq_degenerate_short():
    assert tempo._lstsq([]) == (0.0, 0.0, 0.0)
    assert tempo._lstsq([7.0]) == (0.0, 7.0, 0.0)


def test_lstsq_negative_slope():
    slope, _, _ = tempo._lstsq([4.0, 3.0, 2.0, 1.0, 0.0])
    assert abs(slope - (-1.0)) < 1e-9


def test_lstsq_residual_positive_on_noise():
    slope, intercept, resid = tempo._lstsq([0.0, 2.0, 1.0, 3.0, 2.0])
    assert resid > 0.0


# --------------------------------------------------------------------------- #
# helper: _classify
# --------------------------------------------------------------------------- #
def test_classify_helper():
    assert tempo._classify(0.5, 0.1) == "rising"
    assert tempo._classify(-0.5, 0.1) == "falling"
    assert tempo._classify(0.0, 0.1) == "steady"
    assert tempo._classify(0.1, 0.1) == "rising"      # boundary inclusive
    assert tempo._classify(-0.1, 0.1) == "falling"
    assert tempo._classify(0.05, 0.1) == "steady"


def test_classify_threshold_is_abs():
    assert tempo._classify(0.3, -0.2) == "rising"  # negative threshold treated as abs


# --------------------------------------------------------------------------- #
# helper: _group_key fallbacks
# --------------------------------------------------------------------------- #
def test_group_key_region_default():
    e = ConflictEvent(region="R", country="C", location="L")
    assert tempo._group_key(e, "region") == "R"


def test_group_key_region_falls_back_to_country():
    e = ConflictEvent(country="C", location="L")
    assert tempo._group_key(e, "region") == "C"


def test_group_key_country_lens():
    e = ConflictEvent(region="R", country="C")
    assert tempo._group_key(e, "country") == "C"


def test_group_key_location_lens():
    e = ConflictEvent(region="R", country="C", location="L")
    assert tempo._group_key(e, "location") == "L"


# --------------------------------------------------------------------------- #
# determinism
# --------------------------------------------------------------------------- #
def test_deterministic_under_reordering():
    evs = _two_region()
    a = json.dumps(tempo.summary(evs), sort_keys=True)
    b = json.dumps(tempo.summary(list(reversed(evs))), sort_keys=True)
    assert a == b


def test_deterministic_repeated_calls():
    evs = _two_region()
    assert tempo.forecast(evs) == tempo.forecast(evs)


def test_duplicate_events_counted_each():
    # tempo is a volume measure — identical reports on a day both count
    one = tempo.region_tempo(_flat(30, 1, region="North"), "North", window=7)
    two = tempo.region_tempo(_flat(30, 2, region="North"), "North", window=7)
    assert two["recent_rate"] == 2 * one["recent_rate"]


# --------------------------------------------------------------------------- #
# parametrized property sweeps
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("window", [3, 5, 7, 14])
@pytest.mark.parametrize("horizon", [1, 3, 7, 14])
def test_projection_bounds_across_params(window, horizon):
    evs = _flat(90, 3, region="North") + _span(6, 0, 12, region="North")
    r = tempo.region_tempo(evs, "North", window=window, horizon=horizon)
    assert r is not None
    assert len(r["projection"]) == horizon
    for p in r["projection"]:
        assert p["value"] >= 0.0
        assert p["lo"] <= p["value"] <= p["hi"]


@pytest.mark.parametrize("fit_days", [7, 14, 28, 60])
def test_fit_days_sweep_bounded(fit_days):
    evs = _flat(120, 3, region="North") + _span(6, 0, 15, region="North")
    r = tempo.region_tempo(evs, "North", fit_days=fit_days)
    assert r["fit_days"] <= r["days"]
    assert r["fit_days"] >= 2


@pytest.mark.parametrize("surge_pd", [8, 12, 20, 40])
def test_bigger_surge_never_lowers_slope(surge_pd):
    small = tempo.region_tempo(_surge(region="N", surge_pd=8), "N")
    big = tempo.region_tempo(_surge(region="N", surge_pd=surge_pd), "N")
    assert big["slope_per_day"] >= small["slope_per_day"]


@pytest.mark.parametrize("per_day", [1, 2, 5, 10])
def test_flat_any_level_stays_steady(per_day):
    r = tempo.region_tempo(_flat(90, per_day, region="North"), "North")
    assert r["trend"] == "steady"


@pytest.mark.parametrize("rise", [0.01, 0.1, 0.5, 2.0])
def test_higher_threshold_never_more_rising(rise):
    evs = _two_region()
    loose = tempo.forecast(evs, rise_threshold=rise)
    strict = tempo.forecast(evs, rise_threshold=5.0)
    n_loose = sum(1 for r in loose if r["trend"] == "rising")
    n_strict = sum(1 for r in strict if r["trend"] == "rising")
    assert n_loose >= n_strict


@pytest.mark.parametrize("by", ["region", "country", "location"])
def test_all_group_lenses_produce_valid_board(by):
    evs = _flat(30, 2, region="North", country="X", location="TownA") \
        + _surge(region="South", country="Y", location="TownB")
    board = tempo.forecast(evs, by=by)
    assert board
    for r in board:
        assert r["trend"] in tempo.TREND_CLASSES
        assert r["projected_total"] >= 0.0


@pytest.mark.parametrize("min_days", [2, 3, 5, 10])
def test_min_days_filters_short_regions(min_days):
    evs = _flat(4, 2, region="Short") + _flat(30, 2, region="Long")
    board = tempo.forecast(evs, min_days=min_days)
    keys = {r["region"] for r in board}
    if min_days <= 4:
        assert "Short" in keys
    else:
        assert "Short" not in keys
    assert "Long" in keys


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _write_events(tmp_path, events):
    p = tmp_path / "tempo_events.json"
    p.write_text(json.dumps([e.to_dict() for e in events]), encoding="utf-8")
    return str(p)


def _run(argv, capsys):
    rc = main(argv)
    return rc, capsys.readouterr().out


def test_cli_tempo_table(tmp_path, capsys):
    path = _write_events(tmp_path, _two_region())
    rc, out = _run(["tempo", path], capsys)
    assert rc == 0
    assert "event-tempo forecast" in out.lower()
    assert "board" in out.lower()


def test_cli_tempo_json(tmp_path, capsys):
    path = _write_events(tmp_path, _two_region())
    rc, out = _run(["tempo", path, "--format", "json"], capsys)
    assert rc == 0
    data = json.loads(out)
    assert "board" in data and "rising" in data
    assert data["regions"] == len(data["board"])


def test_cli_tempo_by_country(tmp_path, capsys):
    evs = _flat(30, 2, region="North", country="Aland") \
        + _flat(30, 3, region="South", country="Bland")
    path = _write_events(tmp_path, evs)
    rc, out = _run(["tempo", path, "--by", "country", "--format", "json"], capsys)
    assert rc == 0
    keys = {r["region"] for r in json.loads(out)["board"]}
    assert keys == {"Aland", "Bland"}


def test_cli_tempo_as_of(tmp_path, capsys):
    path = _write_events(tmp_path, _surge(region="North"))
    rc, out = _run(["tempo", path, "--as-of", "2026-06-10", "--format", "json"], capsys)
    assert rc == 0
    board = json.loads(out)["board"]
    assert board
    assert board[0]["projection"][0]["date"] == "2026-06-11"


def test_cli_tempo_window_horizon(tmp_path, capsys):
    path = _write_events(tmp_path, _surge(region="North"))
    rc, out = _run(["tempo", path, "--window", "5", "--horizon", "3",
                    "--format", "json"], capsys)
    assert rc == 0
    board = json.loads(out)["board"]
    assert all(len(r["projection"]) == 3 for r in board)
