"""escalation — a rolling escalation index over a stream of normalized ConflictEvents.

`watch` fires discrete, explainable *alerts* when a detector crosses a threshold.
`trends` reads the *shape* of a single daily series. This module answers the
adjacent question a watch officer actually tracks hour to hour: **on one bounded
0-100 dial, how escalatory is the picture right now, and is that dial rising or
falling?**

The escalation index blends three orthogonal, deterministic signals — the three
things that independently make a situation "more escalated" than its own recent
baseline:

  * **tempo**      — how much is happening. Recent event count over a trailing
                     window vs. the typical per-window rate in the baseline.
  * **intensity**  — how deadly it is. Recent lethality (fatalities per event)
                     vs. the baseline lethality — the *character* of violence, not
                     just its volume.
  * **spread**     — how wide it is. Distinct active locations in the recent window
                     vs. the typical per-window footprint in the baseline (a front
                     widening / conflict diffusing).

Each signal is a ratio of *recent* to *own baseline*, so the index measures
escalation — elevation above a theatre's normal — rather than absolute violence: a
brutal but steady conflict reads low, a quiet theatre suddenly flaring reads high.
Each ratio is mapped through a bounded saturating curve to a 0-100 component score,
the three are combined by weight, and the result is damped by absolute recent volume
so a 0->2 blip can never light up the dial. A trajectory tag (rising / falling /
steady) comes from comparing the index to itself one window earlier.

Scope: descriptive open-source early-warning for awareness, force protection, and
humanitarian response. This summarizes *reported* escalation for human review — it
does not target, recommend force, or task collection. Pure standard library,
deterministic, offline.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta
from typing import Iterable, Optional

from conflictwatch.events import ConflictEvent

# the three index components (stable strings used in output + tests)
COMPONENTS = ("tempo", "intensity", "spread")

# default component weights (normalized internally; tempo leads, spread supports)
DEFAULT_WEIGHTS = {"tempo": 0.40, "intensity": 0.35, "spread": 0.25}

# escalation levels on the 0-100 dial, ascending (calm .. severe)
LEVELS = ("calm", "guarded", "elevated", "high", "severe")
_LEVEL_BOUNDS = (20.0, 40.0, 60.0, 80.0)  # < -> calm, then guarded, elevated, high, >= severe

# tuning constants (documented so an analyst can audit every number)
_SPAN = 1.5          # saturation span: ratio 2.5x -> ~63, 4x -> ~86, ∞ -> 100
_VOL_FULL = 6.0      # recent events needed for full (undamped) index weight
_TEMPO_FLOOR = 1.0   # min baseline per-window tempo (avoids divide-by-quiet blow-ups)
_LETH_FLOOR = 0.5    # min baseline lethality
_SPREAD_FLOOR = 1.0  # min baseline footprint
_RISE = 5.0          # index-point move (window-over-window) to call rising/falling


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _parse(d: str) -> Optional[date]:
    try:
        return date.fromisoformat(d)
    except (ValueError, TypeError):
        return None


def _loc_key(e: ConflictEvent) -> str:
    return e.location or e.region or e.country or ""


def _in_scope(e: ConflictEvent, country: Optional[str], region: Optional[str]) -> bool:
    if country is not None and (e.country or "") != country:
        return False
    if region is not None and (e.region or "") != region:
        return False
    return True


def _norm_weights(weights: Optional[dict]) -> dict:
    w = dict(DEFAULT_WEIGHTS) if not weights else {
        c: float(weights.get(c, 0.0)) for c in COMPONENTS}
    w = {c: max(0.0, w.get(c, 0.0)) for c in COMPONENTS}
    total = sum(w.values())
    if total <= 0:
        return dict(DEFAULT_WEIGHTS)
    return {c: w[c] / total for c in COMPONENTS}


def _saturate(ratio: float) -> float:
    """Map a recent/baseline ratio to a bounded 0-100 escalation component score.

    Only escalation (ratio above 1) contributes; the curve saturates so runaway
    ratios cannot dominate. ratio<=1 -> 0, 2.5x -> ~63, 4x -> ~86, ∞ -> 100.
    """
    x = max(0.0, float(ratio) - 1.0)
    return 100.0 * (1.0 - math.exp(-x / _SPAN))


def _dense(events, lo: date, hi: date):
    """Per-day {events, fatalities, locs:set} for every calendar day in [lo, hi]."""
    ev = defaultdict(int)
    fat = defaultdict(int)
    locs: dict[date, set] = defaultdict(set)
    for e in events:
        d = _parse(e.date)
        if d and lo <= d <= hi:
            ev[d] += 1
            fat[d] += e.fatalities
            k = _loc_key(e)
            if k:
                locs[d].add(k)
    days = []
    cur = lo
    while cur <= hi:
        days.append({"date": cur, "events": ev.get(cur, 0),
                     "fatalities": fat.get(cur, 0), "locs": locs.get(cur, set())})
        cur += timedelta(days=1)
    return days


def _mean_window_spread(days, win: int) -> float:
    """Mean distinct-location footprint across every full sliding window in ``days``."""
    n = len(days)
    if n == 0:
        return 0.0
    if n < win:
        u: set = set()
        for d in days:
            u |= d["locs"]
        return float(len(u))
    counts = []
    for s in range(0, n - win + 1):
        u = set()
        for i in range(s, s + win):
            u |= days[i]["locs"]
        counts.append(len(u))
    return sum(counts) / len(counts)


def _level_of(index: float) -> str:
    for i, bound in enumerate(_LEVEL_BOUNDS):
        if index < bound:
            return LEVELS[i]
    return LEVELS[-1]


def _classify(delta: Optional[float], rise: float) -> str:
    if delta is None:
        return "steady"
    if delta >= rise:
        return "rising"
    if delta <= -rise:
        return "falling"
    return "steady"


# --------------------------------------------------------------------------- #
# core: one day's index
# --------------------------------------------------------------------------- #
def _point(days, i: int, win: int, base_days: int, weights: dict):
    """Compute the escalation index for day index ``i`` of the dense series.

    Returns a dict (date/index/components/volume) or ``None`` if there is not
    enough trailing history to form a baseline for this day.
    """
    recent_start = i - win + 1
    if recent_start < 0:
        return None
    base_end = recent_start - 1
    base_start = max(0, base_end - base_days + 1)
    nb = base_end - base_start + 1
    if base_end < 0 or nb < win:
        return None  # need at least one full window of baseline

    recent = days[recent_start:i + 1]
    baseline = days[base_start:base_end + 1]

    rec_ev = sum(d["events"] for d in recent)
    rec_fat = sum(d["fatalities"] for d in recent)
    rec_locs: set = set()
    for d in recent:
        rec_locs |= d["locs"]
    rec_leth = rec_fat / rec_ev if rec_ev else 0.0

    base_ev = sum(d["events"] for d in baseline)
    base_fat = sum(d["fatalities"] for d in baseline)
    base_tempo_pw = (base_ev / nb) * win
    base_leth = base_fat / base_ev if base_ev else 0.0
    base_spread = _mean_window_spread(baseline, win)

    tempo_ratio = rec_ev / max(base_tempo_pw, _TEMPO_FLOOR)
    # intensity only escalates when there are actually recent fatalities
    leth_ratio = (rec_leth / max(base_leth, _LETH_FLOOR)) if rec_fat > 0 else 0.0
    spread_ratio = len(rec_locs) / max(base_spread, _SPREAD_FLOOR)

    c_tempo = _saturate(tempo_ratio)
    c_intensity = _saturate(leth_ratio)
    c_spread = _saturate(spread_ratio)

    combined = (weights["tempo"] * c_tempo
                + weights["intensity"] * c_intensity
                + weights["spread"] * c_spread)
    damp = min(1.0, rec_ev / _VOL_FULL)
    index = combined * damp

    return {
        "date": days[i]["date"].isoformat(),
        "index": round(index, 1),
        "tempo": round(c_tempo, 1),
        "intensity": round(c_intensity, 1),
        "spread": round(c_spread, 1),
        "volume": rec_ev,
        "fatalities": rec_fat,
        "locations": len(rec_locs),
    }


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def index_series(events: Iterable[ConflictEvent], *, window: int = 7,
                 baseline_windows: int = 4, weights: Optional[dict] = None,
                 country: Optional[str] = None, region: Optional[str] = None,
                 as_of: Optional[str] = None, rise_threshold: float = _RISE
                 ) -> list[dict]:
    """Rolling escalation index for every day with enough trailing baseline.

    Args:
        events: iterable of ConflictEvent (or anything with the event attributes).
        window: recent-window length in days (the "now" being scored).
        baseline_windows: how many windows of history form the baseline.
        weights: optional {tempo,intensity,spread} weights (normalized internally).
        country / region: optional filters to score a single theatre.
        as_of: ISO date to evaluate up to (defaults to the latest event date); lets
            you replay the index as it would have read on a past day.
        rise_threshold: index-point move (window-over-window) to tag rising/falling.

    Returns an ascending list of ``{date, index, level, direction, delta,
    tempo, intensity, spread, volume, fatalities, locations}`` — one row per
    scored day, ``index`` on a bounded 0-100 dial.
    """
    win = max(1, int(window))
    base_days = win * max(1, int(baseline_windows))
    w = _norm_weights(weights)

    evs = [e for e in events
           if getattr(e, "date", "") and _parse(e.date) and _in_scope(e, country, region)]
    if not evs:
        return []

    lo = min(_parse(e.date) for e in evs)
    hi = max(_parse(e.date) for e in evs)
    cutoff = _parse(as_of) if (as_of and _parse(as_of)) else hi
    if cutoff < lo:
        return []
    hi = min(hi, cutoff)

    days = _dense(evs, lo, hi)
    points = []
    for i in range(len(days)):
        pt = _point(days, i, win, base_days, w)
        if pt is not None:
            points.append(pt)

    # trajectory: compare each day's index to itself one window earlier
    by_date = {p["date"]: p["index"] for p in points}
    for p in points:
        d = _parse(p["date"])
        prior = by_date.get((d - timedelta(days=win)).isoformat())
        delta = round(p["index"] - prior, 1) if prior is not None else None
        p["delta"] = delta
        p["level"] = _level_of(p["index"])
        p["direction"] = _classify(delta, rise_threshold)
    return points


def _slope(values: list[float]) -> float:
    """Least-squares slope (per step) of a short index series."""
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(values) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    return sum((xs[i] - mx) * (values[i] - my) for i in range(n)) / denom


def current(events, *, window: int = 7, **kwargs) -> dict:
    """Snapshot of the latest scored day: index, level, trajectory, and drivers.

    Returns ``{date, index, level, direction, delta, slope_per_day, components,
    drivers, volume, fatalities, locations, points}`` — ``drivers`` ranks the three
    components by their weighted contribution so an analyst sees *why* the dial is
    where it is. When there is no scorable history, a calm zeroed snapshot is
    returned rather than raising.
    """
    weights = _norm_weights(kwargs.get("weights"))
    series = index_series(events, window=window, **kwargs)
    if not series:
        return {
            "date": None, "index": 0.0, "level": "calm", "direction": "steady",
            "delta": None, "slope_per_day": 0.0,
            "components": {c: 0.0 for c in COMPONENTS},
            "drivers": [], "volume": 0, "fatalities": 0, "locations": 0, "points": 0,
        }
    last = series[-1]
    win = max(1, int(window))
    tail = [p["index"] for p in series[-win:]]
    slope = _slope(tail)
    components = {c: last[c] for c in COMPONENTS}
    drivers = sorted(
        ({"component": c, "score": last[c], "weight": round(weights[c], 3),
          "contribution": round(weights[c] * last[c], 1)} for c in COMPONENTS),
        key=lambda d: d["contribution"], reverse=True)
    return {
        "date": last["date"],
        "index": last["index"],
        "level": last["level"],
        "direction": last["direction"],
        "delta": last["delta"],
        "slope_per_day": round(slope, 3),
        "components": components,
        "drivers": drivers,
        "volume": last["volume"],
        "fatalities": last["fatalities"],
        "locations": last["locations"],
        "points": len(series),
    }


def summary(events, **kwargs) -> dict:
    """Compact escalation roll-up over the whole scored span.

    Returns the ``current`` snapshot plus the series length, the peak escalation
    day, and how many days were rising vs. falling — a one-glance escalation card.
    """
    series = index_series(events, **{k: v for k, v in kwargs.items()})
    cur = current(events, **kwargs)
    peak = max(series, key=lambda p: p["index"]) if series else None
    rising = sum(1 for p in series if p["direction"] == "rising")
    falling = sum(1 for p in series if p["direction"] == "falling")
    return {
        "current": cur,
        "points": len(series),
        "peak": {"date": peak["date"], "index": peak["index"]} if peak else None,
        "rising_days": rising,
        "falling_days": falling,
        "series": series,
    }
