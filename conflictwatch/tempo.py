"""tempo — near-term event-tempo forecasting, per region, for defensive early-warning.

`trends.forecast` projects a *single* daily series forward; `escalation` scores how
elevated one theatre is right now. This module answers the question a watch officer
asks across a whole map at once: **which regions are picking up pace, and roughly how
many events should we brace for in each over the next few days?**

For every region in a stream of normalized ConflictEvents it builds a dense, zero-filled
daily event-count series, reads the *tempo* out of it, and projects near-term event
volume forward:

  * **recent rate**  — mean events/day over the trailing ``window`` (the pace "now").
  * **prior rate**   — mean events/day over the ``window`` immediately before that, so
                       momentum (recent − prior) is visible.
  * **slope**        — least-squares trend (events/day change per day) over the last
                       ``fit_days`` of the series — the line that gets extrapolated.
  * **projection**   — ``horizon`` days of projected daily counts (clamped at zero) with
                       a simple residual-based confidence band, plus a projected total.
  * **trend class**  — ``rising`` / ``steady`` / ``falling`` from the slope against a
                       documented threshold, so a region self-classifies for triage.

Every region is scored the same way and the board is ranked by projected volume, giving
an at-a-glance early-warning list: where tempo is climbing and where it is easing.

Scope: descriptive open-source early-warning for awareness, force protection, logistics
planning and humanitarian response. It projects *reported* event volume for human review
— it does not target, task collection, recommend force, or nominate anything. The
projection is a naive trend line for context, explicitly not a model to act on blindly.
Pure standard library, deterministic, offline.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Iterable, Optional

from conflictwatch.events import ConflictEvent

# trend classes (stable strings used in output + tests), ascending in tempo
TREND_CLASSES = ("falling", "steady", "rising")

# how a region can be grouped ("region" is the natural admin1 lens; country/location
# let an analyst zoom out or in). The default is region — the module's namesake.
GROUP_BY = ("region", "country", "location")

# defaults (documented so an analyst can audit every number)
DEFAULT_WINDOW = 7        # trailing days that define the "recent" pace
DEFAULT_FIT_DAYS = 28     # days of history the trend line is fit to
DEFAULT_HORIZON = 7       # days projected forward
DEFAULT_MIN_DAYS = 3      # min series length (days) before a region is forecastable
DEFAULT_RISE = 0.10       # |slope| (events/day/day) to call rising / falling vs steady
_BAND_K = 1.0             # residual-std multiplier for the projection confidence band


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _parse(d: str) -> Optional[date]:
    try:
        return date.fromisoformat(d)
    except (ValueError, TypeError):
        return None


def _group_key(e: ConflictEvent, by: str) -> str:
    """The grouping label for an event under ``by`` (falls back sensibly)."""
    if by == "country":
        return e.country or e.region or ""
    if by == "location":
        return e.location or e.region or e.country or ""
    return e.region or e.country or ""  # default: region


def _in_country(e: ConflictEvent, country: Optional[str]) -> bool:
    return country is None or (e.country or "") == country


def _mean(xs) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _lstsq(ys: list[float]):
    """Least-squares fit of ``ys`` against 0..n-1.

    Returns ``(slope, intercept, resid_std)``. ``slope``/``intercept`` are 0/mean for a
    degenerate (n<2 or flat-x) series; ``resid_std`` is the population std of residuals
    (0 for n<2), used only to draw a descriptive confidence band.
    """
    n = len(ys)
    if n < 2:
        return 0.0, (ys[0] if ys else 0.0), 0.0
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return 0.0, my, 0.0
    slope = sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / denom
    intercept = my - slope * mx
    resid = [ys[i] - (slope * xs[i] + intercept) for i in range(n)]
    resid_std = (sum(r * r for r in resid) / n) ** 0.5
    return slope, intercept, resid_std


def _classify(slope: float, rise: float) -> str:
    r = abs(float(rise))
    if slope >= r:
        return "rising"
    if slope <= -r:
        return "falling"
    return "steady"


def _region_series(events, by, country, as_of):
    """Build ``{key: [counts...]}`` dense daily series sharing a common end date.

    All in-scope, dated events (optionally filtered to one ``country``, and truncated at
    ``as_of``) are bucketed by group key and day. Every region is zero-filled from its
    own first event day through the shared end day (the latest event date, or ``as_of``),
    so a region that has gone quiet correctly shows a run of trailing zeros. Returns
    ``(series_by_key, end_date)`` or ``({}, None)`` when there is nothing in scope.
    """
    per: dict[str, dict] = defaultdict(lambda: defaultdict(int))
    dates: list[date] = []
    for e in events:
        d = _parse(getattr(e, "date", ""))
        if not d or not _in_country(e, country):
            continue
        key = _group_key(e, by)
        if not key:
            continue
        per[key][d] += 1
        dates.append(d)
    if not dates:
        return {}, None
    hi = max(dates)
    cutoff = _parse(as_of) if (as_of and _parse(as_of)) else hi
    if cutoff < min(dates):
        return {}, None
    hi = min(hi, cutoff)

    series: dict[str, list[int]] = {}
    for key, days in per.items():
        kept = {d: c for d, c in days.items() if d <= hi}
        if not kept:
            continue
        lo = min(kept)
        row, cur = [], lo
        while cur <= hi:
            row.append(kept.get(cur, 0))
            cur += timedelta(days=1)
        series[key] = row
    return series, hi


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def regions(events: Iterable[ConflictEvent], *, by: str = "region",
            country: Optional[str] = None, as_of: Optional[str] = None) -> list[str]:
    """Sorted list of forecastable region keys in scope (any dated activity)."""
    series, _ = _region_series(events, by, country, as_of)
    return sorted(series)


def _tempo_from_series(row: list[int], end: date, *, window: int, fit_days: int,
                       horizon: int, rise_threshold: float) -> dict:
    """Core: read tempo + project one region's dense daily count series forward."""
    win = max(1, int(window))
    hz = max(1, int(horizon))
    seg = row[-max(2, int(fit_days)):] if len(row) >= 2 else list(row)
    n = len(seg)

    recent = row[-win:]
    prior = row[-2 * win:-win]
    recent_rate = _mean(recent)
    prior_rate = _mean(prior) if prior else None
    momentum = round(recent_rate - prior_rate, 2) if prior_rate is not None else None

    slope, intercept, resid_std = _lstsq([float(v) for v in seg])
    band = _BAND_K * resid_std
    proj = []
    total = 0.0
    for h in range(1, hz + 1):
        x = n - 1 + h
        val = max(0.0, slope * x + intercept)
        total += val
        proj.append({
            "date": (end + timedelta(days=h)).isoformat(),
            "value": round(val, 2),
            "lo": round(max(0.0, val - band), 2),
            "hi": round(val + band, 2),
        })

    return {
        "days": len(row),
        "fit_days": n,
        "recent_rate": round(recent_rate, 2),
        "prior_rate": round(prior_rate, 2) if prior_rate is not None else None,
        "momentum": momentum,
        "slope_per_day": round(slope, 3),
        "trend": _classify(slope, rise_threshold),
        "recent_total": sum(recent),
        "horizon": hz,
        "projected_total": round(total, 1),
        "projection": proj,
    }


def region_tempo(events: Iterable[ConflictEvent], region: str, *, by: str = "region",
                 window: int = DEFAULT_WINDOW, fit_days: int = DEFAULT_FIT_DAYS,
                 horizon: int = DEFAULT_HORIZON, min_days: int = DEFAULT_MIN_DAYS,
                 country: Optional[str] = None, as_of: Optional[str] = None,
                 rise_threshold: float = DEFAULT_RISE) -> Optional[dict]:
    """Tempo read + near-term projection for a single ``region`` key.

    Returns a row (see :func:`forecast`) or ``None`` when the region has no in-scope
    activity or fewer than ``min_days`` days of series to fit a trend to.
    """
    series, end = _region_series(events, by, country, as_of)
    row = series.get(region)
    if not row or end is None or len(row) < max(2, int(min_days)):
        return None
    out = {"region": region}
    out.update(_tempo_from_series(row, end, window=window, fit_days=fit_days,
                                  horizon=horizon, rise_threshold=rise_threshold))
    return out


def forecast(events: Iterable[ConflictEvent], *, by: str = "region",
             window: int = DEFAULT_WINDOW, fit_days: int = DEFAULT_FIT_DAYS,
             horizon: int = DEFAULT_HORIZON, min_days: int = DEFAULT_MIN_DAYS,
             country: Optional[str] = None, as_of: Optional[str] = None,
             rise_threshold: float = DEFAULT_RISE) -> list[dict]:
    """Per-region event-tempo forecast board, ranked by projected near-term volume.

    Args:
        events: iterable of ConflictEvent (or anything with the event attributes).
        by: grouping lens — ``region`` (default), ``country`` or ``location``.
        window: trailing days defining the recent pace and momentum.
        fit_days: days of history the trend line is fit to.
        horizon: days projected forward.
        min_days: minimum series length before a region is forecastable.
        country: optional filter to one country's regions.
        as_of: ISO date to project from (replay a past day); defaults to latest event.
        rise_threshold: |slope| (events/day/day) to tag rising / falling vs steady.

    Returns a list of rows, highest ``projected_total`` first (ties broken by region
    name), each ``{region, days, fit_days, recent_rate, prior_rate, momentum,
    slope_per_day, trend, recent_total, horizon, projected_total, projection}``.
    ``projection`` is ``[{date, value, lo, hi}]`` over the horizon. Empty when nothing
    is in scope.
    """
    series, end = _region_series(events, by, country, as_of)
    if end is None:
        return []
    floor = max(2, int(min_days))
    rows = []
    for key in sorted(series):
        row = series[key]
        if len(row) < floor:
            continue
        r = {"region": key}
        r.update(_tempo_from_series(row, end, window=window, fit_days=fit_days,
                                    horizon=horizon, rise_threshold=rise_threshold))
        rows.append(r)
    rows.sort(key=lambda r: (-r["projected_total"], r["region"]))
    return rows


def summary(events: Iterable[ConflictEvent], *, top: int = 5, **kwargs) -> dict:
    """Compact tempo roll-up over every forecastable region.

    Returns ``{as_of, horizon, regions, rising, steady, falling, projected_total,
    top_rising, board}`` — a one-glance early-warning card: how many regions are
    accelerating, the total near-term event volume projected across the map, and the
    fastest-rising regions (by projected volume) first.
    """
    board = forecast(events, **kwargs)
    counts = {c: 0 for c in TREND_CLASSES}
    for r in board:
        counts[r["trend"]] += 1
    rising = [r for r in board if r["trend"] == "rising"]
    rising.sort(key=lambda r: (-r["slope_per_day"], -r["projected_total"], r["region"]))
    n = max(0, int(top))
    return {
        "as_of": kwargs.get("as_of"),
        "horizon": board[0]["horizon"] if board else int(kwargs.get("horizon",
                                                                     DEFAULT_HORIZON)),
        "regions": len(board),
        "rising": counts["rising"],
        "steady": counts["steady"],
        "falling": counts["falling"],
        "projected_total": round(sum(r["projected_total"] for r in board), 1),
        "top_rising": [{"region": r["region"], "slope_per_day": r["slope_per_day"],
                        "projected_total": r["projected_total"]} for r in rising[:n]],
        "board": board,
    }
