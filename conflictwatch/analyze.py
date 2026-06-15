"""Analysis over normalized ConflictEvents — hotspots, timeline, actors, trends.

Turns a pile of events into situational awareness: where activity concentrates, how it
moves over time, who is active, and what's escalating. Pure stdlib, deterministic.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta

from conflictwatch.events import ConflictEvent


def hotspots(events: list[ConflictEvent], n: int = 10) -> list[dict]:
    """Top locations by event count and reported fatalities."""
    agg: dict[tuple, dict] = {}
    for e in events:
        key = (e.country, e.region or e.location)
        d = agg.setdefault(key, {"country": e.country, "area": e.region or e.location,
                                 "events": 0, "fatalities": 0})
        d["events"] += 1
        d["fatalities"] += e.fatalities
    ranked = sorted(agg.values(), key=lambda d: (d["fatalities"], d["events"]), reverse=True)
    return ranked[:n]


def timeline(events: list[ConflictEvent]) -> list[dict]:
    """Events and fatalities per day (ascending)."""
    by_day: dict[str, dict] = defaultdict(lambda: {"events": 0, "fatalities": 0})
    for e in events:
        if e.date:
            by_day[e.date]["events"] += 1
            by_day[e.date]["fatalities"] += e.fatalities
    return [{"date": d, **v} for d, v in sorted(by_day.items())]


def actor_activity(events: list[ConflictEvent], n: int = 10) -> list[dict]:
    c: Counter = Counter()
    fat: Counter = Counter()
    for e in events:
        for a in (e.actor1, e.actor2):
            if a:
                c[a] += 1
                fat[a] += e.fatalities
    return [{"actor": a, "events": cnt, "fatalities": fat[a]} for a, cnt in c.most_common(n)]


def by_type(events: list[ConflictEvent]) -> dict:
    return dict(Counter(e.event_type for e in events).most_common())


def trends(events: list[ConflictEvent], window_days: int = 7) -> dict:
    """Compare the most recent window against the prior one (escalation signal)."""
    dated = sorted([e for e in events if e.date], key=lambda e: e.date)
    if not dated:
        return {"recent": 0, "prior": 0, "change_pct": 0.0, "escalating": False}
    try:
        last = date.fromisoformat(dated[-1].date)
    except ValueError:
        return {"recent": len(dated), "prior": 0, "change_pct": 0.0, "escalating": False}
    cut1 = last - timedelta(days=window_days)
    cut2 = last - timedelta(days=2 * window_days)

    def _d(e):
        try:
            return date.fromisoformat(e.date)
        except ValueError:
            return None
    recent = sum(1 for e in dated if (d := _d(e)) and d > cut1)
    prior = sum(1 for e in dated if (d := _d(e)) and cut2 < d <= cut1)
    change = ((recent - prior) / prior * 100.0) if prior else (100.0 if recent else 0.0)
    return {"recent": recent, "prior": prior, "change_pct": round(change, 1),
            "escalating": recent > prior}


def summary(events: list[ConflictEvent], window_days: int = 7) -> dict:
    sev = Counter(e.severity for e in events)
    return {
        "total_events": len(events),
        "total_fatalities": sum(e.fatalities for e in events),
        "by_severity": dict(sev),
        "by_type": by_type(events),
        "hotspots": hotspots(events, 5),
        "top_actors": actor_activity(events, 5),
        "trend": trends(events, window_days),
        "date_range": (
            min((e.date for e in events if e.date), default=""),
            max((e.date for e in events if e.date), default=""),
        ),
    }
