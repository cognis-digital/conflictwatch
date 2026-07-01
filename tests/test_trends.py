"""Tests for conflictwatch.trends — moving average, peaks, lulls, weekday
profile, forecast. Deterministic, offline."""

from __future__ import annotations

import json
import os

from conflictwatch import trends
from conflictwatch.events import ConflictEvent
from conflictwatch.sources import parse_generic_json
from conflictwatch.cli import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "demos", "sample_escalation.json")


def _fixture():
    with open(FIXTURE, encoding="utf-8") as fh:
        return parse_generic_json(fh.read())


def _day(d, n=1, **kw):
    kw.setdefault("country", "X")
    kw.setdefault("event_type", "battle")
    return [ConflictEvent(date=d, **kw) for _ in range(n)]


# --- daily series ------------------------------------------------------------
def test_daily_series_zero_fills():
    evs = _day("2026-06-01", 1) + _day("2026-06-04", 2)
    s = trends.daily_series(evs)
    assert [d["date"] for d in s] == ["2026-06-01", "2026-06-02",
                                      "2026-06-03", "2026-06-04"]
    assert s[0]["value"] == 1 and s[1]["value"] == 0 and s[-1]["value"] == 2


def test_daily_series_empty():
    assert trends.daily_series([]) == []


def test_daily_series_fatalities_metric():
    evs = _day("2026-06-01", 1, fatalities=5)
    s = trends.daily_series(evs, metric="fatalities")
    assert s[0]["value"] == 5


# --- moving average ----------------------------------------------------------
def test_moving_average_smooths():
    ma = trends.moving_average(_fixture(), window=7)
    assert ma and all("ma" in d for d in ma)
    # ma is a rolling mean -> never negative, bounded by max daily value
    assert all(d["ma"] >= 0 for d in ma)


# --- peaks -------------------------------------------------------------------
def test_peaks_flags_surge_days():
    pk = trends.peaks(_fixture())
    assert pk
    assert pk[0]["z"] >= 3.0 and pk[0]["value"] >= 3


def test_peaks_none_on_flat_series():
    evs = []
    for i in range(1, 20):
        evs += _day(f"2026-06-{i:02d}", 2)
    assert trends.peaks(evs) == []


def test_peaks_short_series_empty():
    assert trends.peaks(_day("2026-06-01", 3)) == []


# --- lulls -------------------------------------------------------------------
def test_lulls_detects_quiet_run():
    evs = _day("2026-06-01", 3) + _day("2026-06-07", 3)  # 5 quiet days between
    lu = trends.lulls(evs, min_run=3)
    assert lu and lu[0]["days"] == 5
    assert lu[0]["start"] == "2026-06-02" and lu[0]["end"] == "2026-06-06"


def test_lulls_none_when_busy():
    evs = []
    for i in range(1, 10):
        evs += _day(f"2026-06-{i:02d}", 1)
    assert trends.lulls(evs, min_run=3) == []


# --- weekday profile ---------------------------------------------------------
def test_weekday_profile_seven_days():
    wp = trends.weekday_profile(_fixture())
    assert [w["weekday"] for w in wp] == ["Mon", "Tue", "Wed", "Thu",
                                          "Fri", "Sat", "Sun"]
    assert all(w["mean"] >= 0 for w in wp)


# --- forecast ----------------------------------------------------------------
def test_forecast_direction_rising():
    # strictly increasing series -> rising slope, non-empty projection
    evs = []
    for i in range(1, 15):
        evs += _day(f"2026-06-{i:02d}", i)
    fc = trends.forecast(evs, horizon=5)
    assert fc["direction"] == "rising" and fc["slope_per_day"] > 0
    assert len(fc["projection"]) == 5
    assert all(p["value"] >= 0 for p in fc["projection"])


def test_forecast_flat_series():
    evs = []
    for i in range(1, 15):
        evs += _day(f"2026-06-{i:02d}", 3)
    fc = trends.forecast(evs)
    assert fc["direction"] == "flat"


def test_forecast_projection_never_negative():
    # steeply falling series must clamp projections at zero
    evs = []
    for i, n in enumerate(range(14, 0, -1), start=1):
        evs += _day(f"2026-06-{i:02d}", n)
    fc = trends.forecast(evs, horizon=20)
    assert all(p["value"] >= 0 for p in fc["projection"])


def test_forecast_short_series_safe():
    fc = trends.forecast(_day("2026-06-01", 1))
    assert fc["projection"] == [] and fc["direction"] == "flat"


# --- summary -----------------------------------------------------------------
def test_summary_structure():
    s = trends.summary(_fixture())
    assert set(s) >= {"metric", "moving_average", "peaks", "lulls",
                      "weekday_profile", "forecast"}


def test_deterministic():
    a = json.dumps(trends.summary(_fixture()), sort_keys=True)
    b = json.dumps(trends.summary(list(reversed(_fixture()))), sort_keys=True)
    assert a == b


# --- CLI ---------------------------------------------------------------------
def _run(argv, capsys):
    rc = main(argv)
    return rc, capsys.readouterr().out


def test_cli_trends_table(capsys):
    rc, out = _run(["trends", FIXTURE], capsys)
    assert rc == 0 and "trend" in out.lower() and "weekday" in out.lower()


def test_cli_trends_json(capsys):
    rc, out = _run(["trends", FIXTURE, "--format", "json"], capsys)
    assert rc == 0
    data = json.loads(out)
    assert "forecast" in data and "peaks" in data


def test_cli_trends_fatalities_metric(capsys):
    rc, out = _run(["trends", FIXTURE, "--metric", "fatalities",
                    "--format", "json"], capsys)
    assert rc == 0
    assert json.loads(out)["metric"] == "fatalities"
