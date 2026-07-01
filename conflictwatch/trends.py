"""trends — temporal analytics over a stream of normalized ConflictEvents.

`analyze.timeline` gives you the raw per-day series; this module reads *shape* out
of it — the questions an analyst asks of a time series:

  * **moving_average**  — a smoothed daily series (simple trailing MA) so the trend
                          is visible under day-to-day noise.
  * **peaks**           — local maxima that stand well above the trailing baseline
                          (the bad days worth annotating), robust median+MAD scored.
  * **lulls**           — runs of consecutive quiet days (a fragile calm, a ceasefire
                          holding — or the pause before a push).
  * **weekday_profile** — mean activity by day-of-week (does violence cluster on
                          certain days? a real, documented pattern in some theatres).
  * **forecast**        — a deliberately simple, confidence-bounded linear
                          extrapolation of the recent trend, clearly labelled as a
                          naive descriptive projection (no black-box model).

All descriptive, deterministic, offline. The forecast is a trend line for human
context, not a prediction to act on blindly, and nothing here targets or tasks.
Pure standard library.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from statistics import median
from typing import Iterable, Optional

from conflictwatch.events import ConflictEvent

_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _parse(d: str) -> Optional[date]:
    try:
        return date.fromisoformat(d)
    except (ValueError, TypeError):
        return None


def daily_series(events: Iterable[ConflictEvent], *, metric: str = "events") -> list[dict]:
    """Dense per-day series over the full span (zero-filled gaps).

    ``metric`` is ``events`` (count) or ``fatalities``. Returns
    ``[{date, value}]`` ascending; empty if no dated events.
    """
    ev = defaultdict(int)
    fat = defaultdict(int)
    for e in events:
        d = _parse(e.date)
        if d:
            ev[d] += 1
            fat[d] += e.fatalities
    if not ev:
        return []
    lo, hi = min(ev), max(ev)
    src = fat if metric == "fatalities" else ev
    out, cur = [], lo
    while cur <= hi:
        out.append({"date": cur.isoformat(), "value": src.get(cur, 0)})
        cur += timedelta(days=1)
    return out


def moving_average(events, *, window: int = 7, metric: str = "events") -> list[dict]:
    """Trailing simple moving average of the daily series."""
    series = daily_series(events, metric=metric)
    win = max(1, int(window))
    out = []
    vals = [d["value"] for d in series]
    for i, d in enumerate(series):
        lo = max(0, i - win + 1)
        seg = vals[lo:i + 1]
        out.append({"date": d["date"], "value": d["value"],
                    "ma": round(sum(seg) / len(seg), 2)})
    return out


def _mad(values, med):
    return median([abs(v - med) for v in values]) if values else 0.0


def peaks(events, *, metric: str = "events", min_z: float = 3.0,
          min_value: int = 3) -> list[dict]:
    """Days whose value is a strong positive deviation from the whole series.

    Robust median+MAD z-score over all days; a day is a peak when its z exceeds
    ``min_z`` and its raw value is at least ``min_value``. Returns peaks
    highest-first, each ``{date, value, z}``.
    """
    series = daily_series(events, metric=metric)
    vals = [d["value"] for d in series]
    if len(vals) < 3:
        return []
    med = median(vals)
    mad = _mad(vals, med)
    out = []
    for d in series:
        v = d["value"]
        if v < min_value or v <= med:
            continue
        if mad == 0:
            z = (v - med) / max(med, 1.0) * 3.0
        else:
            z = (v - med) / (1.4826 * mad)
        if z >= min_z:
            out.append({"date": d["date"], "value": v, "z": round(z, 2)})
    out.sort(key=lambda p: (p["value"], p["z"]), reverse=True)
    return out


def lulls(events, *, metric: str = "events", quiet_at_most: int = 0,
          min_run: int = 3) -> list[dict]:
    """Runs of consecutive quiet days (value <= ``quiet_at_most``).

    Returns runs of length >= ``min_run``, longest-first, each
    ``{start, end, days}`` — a fragile calm worth watching.
    """
    series = daily_series(events, metric=metric)
    runs, cur = [], []
    for d in series:
        if d["value"] <= quiet_at_most:
            cur.append(d["date"])
        else:
            if len(cur) >= min_run:
                runs.append({"start": cur[0], "end": cur[-1], "days": len(cur)})
            cur = []
    if len(cur) >= min_run:
        runs.append({"start": cur[0], "end": cur[-1], "days": len(cur)})
    runs.sort(key=lambda r: r["days"], reverse=True)
    return runs


def weekday_profile(events, *, metric: str = "events") -> list[dict]:
    """Mean activity by day-of-week across the series (Mon..Sun order).

    Reveals whether activity clusters on particular weekdays. Uses the dense
    zero-filled series so quiet days count. Returns ``[{weekday, mean, total, days}]``.
    """
    series = daily_series(events, metric=metric)
    buckets: dict[int, list[int]] = defaultdict(list)
    for d in series:
        wd = date.fromisoformat(d["date"]).weekday()
        buckets[wd].append(d["value"])
    out = []
    for wd in range(7):
        vals = buckets.get(wd, [])
        out.append({
            "weekday": _WEEKDAYS[wd],
            "mean": round(sum(vals) / len(vals), 2) if vals else 0.0,
            "total": sum(vals),
            "days": len(vals),
        })
    return out


def forecast(events, *, metric: str = "events", horizon: int = 7,
             fit_days: int = 28) -> dict:
    """Naive least-squares linear extrapolation of the recent daily trend.

    Fits a line to the last ``fit_days`` of the daily series and projects
    ``horizon`` days forward, clamped at zero. This is a *descriptive* trend
    projection for human context — explicitly not a model to act on blindly.
    Returns ``{slope_per_day, direction, fit_days, projection:[{date,value}]}``.
    """
    series = daily_series(events, metric=metric)
    if len(series) < 2:
        return {"slope_per_day": 0.0, "direction": "flat", "fit_days": 0,
                "projection": []}
    seg = series[-max(2, int(fit_days)):]
    n = len(seg)
    xs = list(range(n))
    ys = [d["value"] for d in seg]
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    slope = (sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / denom) if denom else 0.0
    intercept = my - slope * mx
    last_day = date.fromisoformat(seg[-1]["date"])
    proj = []
    for h in range(1, max(1, int(horizon)) + 1):
        x = n - 1 + h
        val = max(0.0, slope * x + intercept)
        proj.append({"date": (last_day + timedelta(days=h)).isoformat(),
                     "value": round(val, 2)})
    direction = "rising" if slope > 0.05 else "falling" if slope < -0.05 else "flat"
    return {"slope_per_day": round(slope, 3), "direction": direction,
            "fit_days": n, "projection": proj}


def summary(events, *, metric: str = "events", window: int = 7,
            horizon: int = 7) -> dict:
    """Compact temporal roll-up across every analytic in this module."""
    return {
        "metric": metric,
        "moving_average": moving_average(events, window=window, metric=metric),
        "peaks": peaks(events, metric=metric),
        "lulls": lulls(events, metric=metric),
        "weekday_profile": weekday_profile(events, metric=metric),
        "forecast": forecast(events, metric=metric, horizon=horizon),
    }
