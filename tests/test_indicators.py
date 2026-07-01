"""Tests for conflictwatch.indicators — defensive I&W posture per scope.
Deterministic, offline; the escalation fixture drives the RED/GREEN separation."""

from __future__ import annotations

import json
import os
from datetime import date, timedelta

import pytest

from conflictwatch import indicators
from conflictwatch.events import ConflictEvent
from conflictwatch.sources import parse_generic_json
from conflictwatch.cli import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "demos", "sample_escalation.json")
END = date(2026, 6, 20)


def _fixture():
    with open(FIXTURE, encoding="utf-8") as fh:
        return parse_generic_json(fh.read())


def _series(country, start_off, end_off, per_day, **kw):
    out = []
    kw.setdefault("event_type", "battle")
    for off in range(start_off, end_off - 1, -1):
        d = (END - timedelta(days=off)).isoformat()
        for _ in range(per_day):
            out.append(ConflictEvent(date=d, country=country, **kw))
    return out


# --- surface -----------------------------------------------------------------
def test_tiers_and_factors_defined():
    assert indicators.TIERS == ("green", "guarded", "amber", "red")
    assert set(indicators.FACTORS) == {"tempo", "lethality", "escalation",
                                       "drone_uas", "spread"}


def test_empty_returns_empty():
    assert indicators.posture([]) == []
    s = indicators.summary([])
    assert s["scopes"] == 0 and s["highest"] == "green" and s["top"] is None


def test_bad_scope_raises():
    with pytest.raises(ValueError):
        indicators.posture([ConflictEvent(date="2026-06-20", country="X")],
                           scope="planet")


# --- posture behavior --------------------------------------------------------
def test_surging_country_is_red_or_amber():
    postures = indicators.posture(_fixture(), scope="country")
    bl = next(p for p in postures if p["scope"] == "Borderland")
    assert bl["tier"] in ("amber", "red")
    assert bl["advisories"]  # drivers surfaced


def test_quiet_country_is_low_tier():
    postures = indicators.posture(_fixture(), scope="country")
    cl = next((p for p in postures if p["scope"] == "Calmland"), None)
    if cl:
        assert cl["tier"] in ("green", "guarded")


def test_factors_bounded_0_1():
    for p in indicators.posture(_fixture(), scope="country"):
        for f in indicators.FACTORS:
            assert 0.0 <= p["factors"][f]["score"] <= 1.0
        assert 0.0 <= p["score"] <= 1.0


def test_lethality_factor_reflects_deadliness():
    # very deadly recent window vs bloodless baseline -> high lethality factor
    evs = (_series("D", 35, 8, 2, fatalities=0)
           + _series("D", 6, 0, 2, fatalities=8))
    p = indicators.posture(evs, scope="country")[0]
    assert p["factors"]["lethality"]["score"] >= 0.5


def test_drone_factor_reflects_threat_types():
    evs = (_series("U", 35, 8, 1, event_type="battle")
           + _series("U", 6, 0, 4, event_type="drone/uas"))
    p = indicators.posture(evs, scope="country")[0]
    assert p["factors"]["drone_uas"]["score"] >= 0.9


def test_postures_sorted_desc():
    postures = indicators.posture(_fixture(), scope="country")
    scores = [p["score"] for p in postures]
    assert scores == sorted(scores, reverse=True)


def test_as_of_before_surge_is_calmer():
    late = indicators.summary(_fixture(), scope="country", as_of="2026-06-20")
    early = indicators.summary(_fixture(), scope="country", as_of="2026-06-10")
    order = {t: i for i, t in enumerate(indicators.TIERS)}
    assert order[early["highest"]] <= order[late["highest"]]


def test_deterministic():
    a = json.dumps(indicators.posture(_fixture()), sort_keys=True)
    b = json.dumps(indicators.posture(list(reversed(_fixture()))), sort_keys=True)
    assert a == b


def test_summary_structure():
    s = indicators.summary(_fixture(), scope="country")
    assert set(s) >= {"scopes", "by_tier", "highest", "top", "postures"}
    assert s["scopes"] == len(s["postures"])


# --- scope guard: advisories are defensive/descriptive -----------------------
def test_advisories_are_defensive():
    blob = json.dumps(indicators._ADVISORIES).lower()
    for bad in ("target", "strike the", "engage the enemy", "fire on", "kill"):
        assert bad not in blob


# --- CLI ---------------------------------------------------------------------
def _run(argv, capsys):
    rc = main(argv)
    return rc, capsys.readouterr().out


def test_cli_posture_table(capsys):
    rc, out = _run(["posture", FIXTURE, "--scope", "country"], capsys)
    assert rc == 0 and "posture" in out.lower() and "Borderland" in out


def test_cli_posture_json(capsys):
    rc, out = _run(["posture", FIXTURE, "--format", "json"], capsys)
    assert rc == 0
    data = json.loads(out)
    assert "postures" in data and data["highest"] in indicators.TIERS


def test_cli_posture_rejects_bad_scope(capsys):
    with pytest.raises(SystemExit):
        _run(["posture", FIXTURE, "--scope", "galaxy"], capsys)
