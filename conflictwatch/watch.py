"""watch — escalation early-warning over a stream of normalized ConflictEvents.

`analyze` answers *what does the picture look like right now*. `watch` answers a
different, harder question that matters for force protection and humanitarian
early-warning: **what is changing, and is it changing fast enough to act on?**

It runs a set of deterministic, dependency-free detectors over a daily activity
series and emits ranked, explainable *alerts* — each with a severity, the evidence
that triggered it, and a plain-language reason. The detectors are intentionally
boring statistics (no models to train, no opaque scoring) so an analyst can audit
every alert by hand:

  * **spike**            — a day/window whose event or fatality count is a large
                           positive deviation (robust z-score over the trailing
                           baseline). Catches sudden flare-ups.
  * **sustained-trend**  — a monotone-ish multi-window rise (recent window vs.
                           prior window above a ratio, with a minimum floor).
                           Catches slow build-ups a single spike test misses.
  * **new-actor**        — an actor that appears in the recent window but is absent
                           from the entire baseline. Catches force composition
                           changes (a new unit, militia, or capability arriving).
  * **geo-spread**       — the count of distinct active locations rising window over
                           window. Catches a front widening / conflict diffusing.
  * **lethality-shift**  — fatalities-per-event rising sharply, i.e. the *character*
                           of violence getting deadlier even if tempo is flat.
  * **new-hotspot**      — a location that crosses an absolute activity floor in the
                           recent window having been quiet (or absent) before.

Severity is derived from how far past threshold a detector fires, then capped by
absolute volume so a 0->2 blip can never outrank a 5->40 surge.

Scope: descriptive open-source early-warning for awareness, force protection, and
humanitarian response. This flags *reported* escalation for human review — it does
not target, recommend force, or task collection. Pure standard library, deterministic.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import median
from typing import Iterable, Optional

from conflictwatch.events import ConflictEvent

# alert severities, ascending
SEVERITIES = ("info", "low", "medium", "high", "critical")

# detector identifiers (stable strings used in output + tests)
DETECTORS = (
    "spike", "sustained-trend", "new-actor", "geo-spread",
    "lethality-shift", "new-hotspot",
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _parse(d: str) -> Optional[date]:
    try:
        return date.fromisoformat(d)
    except (ValueError, TypeError):
        return None


def _dated(events: Iterable[ConflictEvent]) -> list[ConflictEvent]:
    out = [e for e in events if getattr(e, "date", "") and _parse(e.date)]
    out.sort(key=lambda e: e.date)
    return out


def _as_of(events: list[ConflictEvent], as_of: Optional[str]) -> date:
    if as_of and _parse(as_of):
        return _parse(as_of)
    return _parse(events[-1].date)


def _mad(values: list[float], med: float) -> float:
    """Median absolute deviation (robust scale estimate)."""
    if not values:
        return 0.0
    return median([abs(v - med) for v in values])


def _robust_z(value: float, baseline: list[float]) -> float:
    """Robust z-score of ``value`` against a baseline using median + MAD.

    MAD is scaled by 1.4826 to approximate a standard deviation under normality.
    Falls back to a mean/stdev-free heuristic when the baseline is degenerate
    (all-equal): any positive excess over a flat baseline reads as a strong signal.
    """
    if not baseline:
        return 0.0
    med = median(baseline)
    mad = _mad(baseline, med)
    if mad == 0:
        # flat baseline: scale the raw excess by the level so 0->3 on a quiet
        # series is meaningful but 50->53 on a busy one is not.
        if value <= med:
            return 0.0
        denom = max(med, 1.0)
        return (value - med) / denom * 3.0
    return (value - med) / (1.4826 * mad)


def _severity_from(score: float, *, volume: int) -> str:
    """Map a detector score to a severity, capped by absolute volume.

    ``score`` is detector-normalized (roughly: how many thresholds past firing).
    ``volume`` is the absolute event count driving the alert; a tiny-volume alert
    is capped so noise on quiet series cannot reach the top tiers.
    """
    if score >= 4.0:
        tier = "critical"
    elif score >= 3.0:
        tier = "high"
    elif score >= 2.0:
        tier = "medium"
    elif score >= 1.0:
        tier = "low"
    else:
        tier = "info"
    # volume cap
    cap = "critical"
    if volume < 3:
        cap = "low"
    elif volume < 8:
        cap = "medium"
    elif volume < 20:
        cap = "high"
    return _min_sev(tier, cap)


def _min_sev(a: str, b: str) -> str:
    return SEVERITIES[min(SEVERITIES.index(a), SEVERITIES.index(b))]


def _daily_series(events: list[ConflictEvent], lo: date, hi: date):
    """Per-day {events, fatalities} for every calendar day in [lo, hi]."""
    ev = defaultdict(int)
    fat = defaultdict(int)
    for e in events:
        d = _parse(e.date)
        if d and lo <= d <= hi:
            ev[d] += 1
            fat[d] += e.fatalities
    days = []
    cur = lo
    while cur <= hi:
        days.append({"date": cur, "events": ev.get(cur, 0), "fatalities": fat.get(cur, 0)})
        cur += timedelta(days=1)
    return days


# --------------------------------------------------------------------------- #
# core: per-scope escalation evaluation
# --------------------------------------------------------------------------- #
def _scope_of(e: ConflictEvent, scope: str) -> str:
    if scope == "country":
        return e.country or "(unknown)"
    if scope == "region":
        return f"{e.country}/{e.region}" if e.region else (e.country or "(unknown)")
    if scope == "location":
        return e.location or e.region or e.country or "(unknown)"
    if scope == "global":
        return "(all)"
    raise ValueError(f"unknown scope {scope!r}; expected country/region/location/global")


def _alert(detector, scope_name, severity, score, reason, evidence):
    return {
        "detector": detector,
        "scope": scope_name,
        "severity": severity,
        "score": round(float(score), 2),
        "reason": reason,
        "evidence": evidence,
    }


def _eval_scope(scope_name, events, ref, window, baseline_windows):
    """Run every detector for one scope (already filtered to that scope)."""
    alerts = []
    win = max(1, int(window))
    base_days = win * max(1, int(baseline_windows))

    recent_lo = ref - timedelta(days=win - 1)
    base_lo = recent_lo - timedelta(days=base_days)
    base_hi = recent_lo - timedelta(days=1)

    series = _daily_series(events, base_lo, ref)
    by_date = {d["date"]: d for d in series}

    recent = [by_date[d] for d in by_date if recent_lo <= d <= ref]
    baseline = [by_date[d] for d in by_date if base_lo <= d <= base_hi]

    rec_events = sum(d["events"] for d in recent)
    rec_fat = sum(d["fatalities"] for d in recent)

    # ---- spike (per-window event count vs per-window baseline windows) -------
    base_window_counts = _window_totals([d["events"] for d in baseline], win)
    if base_window_counts:
        z = _robust_z(rec_events, base_window_counts)
        if z >= 2.0 and rec_events >= 2:
            sev = _severity_from(z, volume=rec_events)
            alerts.append(_alert(
                "spike", scope_name, sev, z,
                f"{rec_events} events in the last {win}d vs a baseline median of "
                f"{int(median(base_window_counts))}/{win}d (robust z={z:.1f})",
                {"recent_events": rec_events,
                 "baseline_median": median(base_window_counts),
                 "z": round(z, 2), "window_days": win},
            ))

    # ---- sustained-trend (recent window vs immediately-prior window) ---------
    prior_lo = recent_lo - timedelta(days=win)
    prior = [by_date[d] for d in by_date if prior_lo <= d < recent_lo]
    prior_events = sum(d["events"] for d in prior)
    if prior_events >= 2 and rec_events >= prior_events * 1.5 and rec_events >= 4:
        ratio = rec_events / prior_events
        score = 1.0 + (ratio - 1.5)  # 1.5x -> 1.0, 2.5x -> 2.0, ...
        sev = _severity_from(score, volume=rec_events)
        alerts.append(_alert(
            "sustained-trend", scope_name, sev, score,
            f"activity up {ratio:.1f}x window-over-window "
            f"({prior_events} -> {rec_events} events)",
            {"prior_events": prior_events, "recent_events": rec_events,
             "ratio": round(ratio, 2)},
        ))

    # ---- lethality-shift (fatalities per event rising) ----------------------
    base_events = sum(d["events"] for d in baseline)
    base_fat = sum(d["fatalities"] for d in baseline)
    if rec_events >= 3 and base_events >= 3:
        rec_lph = rec_fat / rec_events
        base_lph = base_fat / base_events
        if rec_lph >= 1.0 and rec_lph >= base_lph * 2.0 and (rec_lph - base_lph) >= 1.0:
            score = 1.0 + (rec_lph / max(base_lph, 0.5) - 2.0)
            sev = _severity_from(max(score, 1.0), volume=rec_fat)
            alerts.append(_alert(
                "lethality-shift", scope_name, sev, max(score, 1.0),
                f"lethality rose to {rec_lph:.1f} fatalities/event "
                f"(baseline {base_lph:.1f})",
                {"recent_lethality": round(rec_lph, 2),
                 "baseline_lethality": round(base_lph, 2),
                 "recent_fatalities": rec_fat},
            ))
    return alerts, recent_lo, base_lo, base_hi


def _window_totals(daily_counts: list[int], win: int) -> list[float]:
    """Non-overlapping window sums of a daily series (oldest-first), trailing-aligned."""
    if win <= 0 or not daily_counts:
        return []
    out = []
    # align windows to the END of the series so partial leading window is dropped
    n = len(daily_counts)
    i = n
    while i - win >= 0:
        out.append(float(sum(daily_counts[i - win:i])))
        i -= win
    return list(reversed(out))


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def detect(events, *, scope: str = "country", window: int = 7,
           baseline_windows: int = 4, as_of: Optional[str] = None,
           min_severity: str = "info") -> list[dict]:
    """Run all escalation detectors and return ranked alerts.

    Args:
        events: iterable of ConflictEvent (or anything with the event attributes).
        scope: aggregation unit — ``country`` | ``region`` | ``location`` | ``global``.
        window: recent-window length in days (the "now" the detectors compare).
        baseline_windows: how many windows of history form the baseline.
        as_of: ISO date to evaluate at (defaults to the latest event date). Lets
            you replay early-warning as it would have looked on a past day.
        min_severity: drop alerts below this tier.

    Returns alerts sorted by severity (desc) then score (desc), each a dict with
    ``detector / scope / severity / score / reason / evidence``.
    """
    evs = _dated(list(events))
    if not evs:
        return []
    ref = _as_of(evs, as_of)

    # group events by scope
    groups: dict[str, list[ConflictEvent]] = defaultdict(list)
    for e in evs:
        groups[_scope_of(e, scope)].append(e)

    alerts: list[dict] = []
    for name, group in groups.items():
        scoped_alerts, recent_lo, _, _ = _eval_scope(
            name, group, ref, window, baseline_windows)
        alerts.extend(scoped_alerts)
        # cross-cutting detectors that need the per-scope membership
        alerts.extend(_actor_and_geo_detectors(
            name, group, ref, window, baseline_windows, recent_lo))

    floor = SEVERITIES.index(min_severity) if min_severity in SEVERITIES else 0
    alerts = [a for a in alerts if SEVERITIES.index(a["severity"]) >= floor]
    alerts.sort(key=lambda a: (SEVERITIES.index(a["severity"]), a["score"]), reverse=True)
    return alerts


def _actor_and_geo_detectors(scope_name, events, ref, window, baseline_windows,
                             recent_lo):
    alerts = []
    win = max(1, int(window))
    base_days = win * max(1, int(baseline_windows))
    base_lo = recent_lo - timedelta(days=base_days)
    base_hi = recent_lo - timedelta(days=1)
    prior_lo = recent_lo - timedelta(days=win)

    def in_range(e, lo, hi):
        d = _parse(e.date)
        return d is not None and lo <= d <= hi

    recent_ev = [e for e in events if in_range(e, recent_lo, ref)]
    base_ev = [e for e in events if in_range(e, base_lo, base_hi)]
    prior_ev = [e for e in events if in_range(e, prior_lo, recent_lo - timedelta(days=1))]

    # ---- new-actor ----------------------------------------------------------
    def actors(evlist):
        s = set()
        for e in evlist:
            for a in (e.actor1, e.actor2):
                if a and a.strip():
                    s.add(a.strip())
        return s

    base_actors = actors(base_ev)
    new_actors = sorted(a for a in actors(recent_ev) if a not in base_actors)
    if new_actors and base_ev:
        # weight by how active the new actors are in the recent window
        vol = sum(1 for e in recent_ev
                  if (e.actor1 in new_actors or e.actor2 in new_actors))
        score = 1.0 + min(len(new_actors), 3) * 0.7
        sev = _severity_from(score, volume=max(vol, len(new_actors)))
        alerts.append(_alert(
            "new-actor", scope_name, sev, score,
            f"{len(new_actors)} actor(s) appeared this window absent from "
            f"the {base_days}d baseline: " + ", ".join(new_actors[:5]),
            {"new_actors": new_actors, "recent_event_count": vol},
        ))

    # ---- geo-spread (distinct active locations rising) ----------------------
    def locs(evlist):
        return {(e.location or e.region or e.country) for e in evlist
                if (e.location or e.region or e.country)}

    rec_locs = locs(recent_ev)
    prior_locs = locs(prior_ev)
    if len(prior_locs) >= 1 and len(rec_locs) >= len(prior_locs) + 2 and len(rec_locs) >= 3:
        spread = len(rec_locs) - len(prior_locs)
        score = 1.0 + spread * 0.5
        sev = _severity_from(score, volume=len(recent_ev))
        alerts.append(_alert(
            "geo-spread", scope_name, sev, score,
            f"active locations widened from {len(prior_locs)} to {len(rec_locs)} "
            f"window-over-window (+{spread})",
            {"prior_locations": len(prior_locs),
             "recent_locations": len(rec_locs),
             "new_locations": sorted(rec_locs - prior_locs)[:8]},
        ))

    # ---- new-hotspot (a location crossing an activity floor, quiet before) ---
    rec_loc_counts = Counter(
        (e.location or e.region or e.country) for e in recent_ev
        if (e.location or e.region or e.country))
    base_loc_counts = Counter(
        (e.location or e.region or e.country) for e in base_ev
        if (e.location or e.region or e.country))
    for loc, cnt in rec_loc_counts.items():
        base_cnt = base_loc_counts.get(loc, 0)
        if cnt >= 4 and cnt >= base_cnt * 3 and base_cnt <= 1:
            score = 1.0 + (cnt - 4) * 0.3
            sev = _severity_from(max(score, 1.5), volume=cnt)
            alerts.append(_alert(
                "new-hotspot", scope_name, sev, max(score, 1.5),
                f"'{loc}' surged to {cnt} events this window "
                f"(was {base_cnt} across the {base_days}d baseline)",
                {"location": loc, "recent_events": cnt, "baseline_events": base_cnt},
            ))
    return alerts


def summary(events, **kwargs) -> dict:
    """A compact early-warning roll-up: alert counts by severity + detector."""
    alerts = detect(events, **kwargs)
    by_sev = Counter(a["severity"] for a in alerts)
    by_det = Counter(a["detector"] for a in alerts)
    top = alerts[0] if alerts else None
    return {
        "total_alerts": len(alerts),
        "by_severity": {s: by_sev.get(s, 0) for s in reversed(SEVERITIES) if by_sev.get(s)},
        "by_detector": dict(by_det.most_common()),
        "highest": top["severity"] if top else "info",
        "top_alert": top,
        "alerts": alerts,
    }
