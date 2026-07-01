"""correlate — find structure across a stream of normalized ConflictEvents.

`analyze` answers *what does it look like* and `watch` answers *what is changing*.
`correlate` answers a third question analysts ask constantly: **what goes together?**
It surfaces the relationships an event-by-event read misses —

  * **clusters**        — spatio-temporal groupings of events that are close in
                          *both* place and time (a coordinated push, a bad night in
                          one sector), found with a deterministic single-link grid
                          over haversine distance + a day gap. No sklearn, no deps.
  * **actor-network**   — co-occurrence of actors (who shows up with / against whom),
                          returned as weighted undirected edges — the belligerent
                          graph you can drop into any network tool.
  * **cooccurrence**    — event-type pairs that recur in the same place+window
                          (e.g. "shelling then civilian-harm"), a descriptive pattern
                          signal for awareness.
  * **coordinated**     — days on which activity flares in several distinct locations
                          at once (a broad, simultaneous uptick vs a single hotspot).

Everything here is descriptive open-source correlation for situational awareness —
it links *reported* events for human review. It does not target, task collection,
or recommend force. Pure standard library, deterministic, offline.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Iterable, Optional

from conflictwatch.events import ConflictEvent


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _parse(d: str) -> Optional[date]:
    try:
        return date.fromisoformat(d)
    except (ValueError, TypeError):
        return None


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in km between two lat/lon points (stdlib math)."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def _actors(e: ConflictEvent) -> list[str]:
    return [a.strip() for a in (e.actor1, e.actor2) if a and a.strip()]


def _place(e: ConflictEvent) -> str:
    return e.location or e.region or e.country or "(unknown)"


# --------------------------------------------------------------------------- #
# spatio-temporal clustering
# --------------------------------------------------------------------------- #
def clusters(events: Iterable[ConflictEvent], *, radius_km: float = 50.0,
             max_day_gap: int = 3, min_size: int = 3) -> list[dict]:
    """Group geolocated, dated events that are near in *both* space and time.

    Single-link agglomeration: two events join the same cluster when they are
    within ``radius_km`` and their dates differ by <= ``max_day_gap`` days.
    Only clusters of at least ``min_size`` events are returned. Events lacking a
    coordinate or a parseable date are skipped (they cannot be spatio-temporally
    placed). Deterministic — events are pre-sorted by (date, place).

    Returns clusters sorted by fatalities then size (worst first), each::

        {size, events, fatalities, days:(lo,hi), span_days, centroid:{lat,lon},
         radius_km, countries:[...], actors:[...], event_ids:[...]}
    """
    pts = [e for e in events
           if e.lat is not None and e.lon is not None and _parse(e.date)]
    pts.sort(key=lambda e: (e.date, _place(e)))
    n = len(pts)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    days = [_parse(e.date) for e in pts]
    for i in range(n):
        for j in range(i + 1, n):
            if abs((days[i] - days[j]).days) > max_day_gap:
                continue
            if haversine_km(pts[i].lat, pts[i].lon, pts[j].lat, pts[j].lon) <= radius_km:
                union(i, j)

    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)

    out = []
    for members in groups.values():
        if len(members) < min_size:
            continue
        evs = [pts[i] for i in members]
        lats = [e.lat for e in evs]
        lons = [e.lon for e in evs]
        clat, clon = sum(lats) / len(lats), sum(lons) / len(lons)
        rad = max((haversine_km(clat, clon, e.lat, e.lon) for e in evs), default=0.0)
        ds = sorted(_parse(e.date) for e in evs)
        actors = Counter(a for e in evs for a in _actors(e))
        out.append({
            "size": len(evs),
            "events": len(evs),
            "fatalities": sum(e.fatalities for e in evs),
            "days": (ds[0].isoformat(), ds[-1].isoformat()),
            "span_days": (ds[-1] - ds[0]).days,
            "centroid": {"lat": round(clat, 4), "lon": round(clon, 4)},
            "radius_km": round(rad, 1),
            "countries": sorted({e.country for e in evs if e.country}),
            "actors": [a for a, _ in actors.most_common(6)],
            "event_ids": sorted(e.id for e in evs),
        })
    out.sort(key=lambda c: (c["fatalities"], c["size"]), reverse=True)
    return out


# --------------------------------------------------------------------------- #
# actor co-occurrence network
# --------------------------------------------------------------------------- #
def actor_network(events: Iterable[ConflictEvent], *, min_weight: int = 1) -> dict:
    """Weighted undirected co-occurrence graph of actors.

    Two actors share an edge when they appear on the same event (actor1/actor2 —
    typically the two sides of a clash). Node weight = events the actor appears in
    and fatalities associated. Deterministic ordering. Returns::

        {nodes:[{actor,events,fatalities}], edges:[{source,target,weight,fatalities}]}
    """
    node_ev: Counter = Counter()
    node_fat: Counter = Counter()
    edge_w: Counter = Counter()
    edge_fat: Counter = Counter()
    for e in events:
        acts = sorted(set(_actors(e)))
        for a in acts:
            node_ev[a] += 1
            node_fat[a] += e.fatalities
        for i in range(len(acts)):
            for j in range(i + 1, len(acts)):
                key = (acts[i], acts[j])
                edge_w[key] += 1
                edge_fat[key] += e.fatalities
    nodes = [{"actor": a, "events": c, "fatalities": node_fat[a]}
             for a, c in sorted(node_ev.items(), key=lambda kv: (-kv[1], kv[0]))]
    edges = [{"source": s, "target": t, "weight": w, "fatalities": edge_fat[(s, t)]}
             for (s, t), w in sorted(edge_w.items(), key=lambda kv: (-kv[1], kv[0]))
             if w >= min_weight]
    return {"nodes": nodes, "edges": edges}


# --------------------------------------------------------------------------- #
# event-type co-occurrence in the same place + window
# --------------------------------------------------------------------------- #
def cooccurrence(events: Iterable[ConflictEvent], *, window_days: int = 3,
                 min_count: int = 2) -> list[dict]:
    """Event-type pairs that recur in the same place within ``window_days``.

    For each place, sort events by date; any two events whose types differ and
    whose dates are within the window count as a co-occurrence of that (sorted)
    type pair. Returns pairs seen at least ``min_count`` times, most frequent
    first — a descriptive "these tend to happen together" signal.
    """
    by_place: dict[str, list[ConflictEvent]] = defaultdict(list)
    for e in events:
        if _parse(e.date):
            by_place[_place(e)].append(e)
    pair_count: Counter = Counter()
    pair_places: dict[tuple, set] = defaultdict(set)
    for place, evs in by_place.items():
        evs = sorted(evs, key=lambda e: e.date)
        for i in range(len(evs)):
            di = _parse(evs[i].date)
            for j in range(i + 1, len(evs)):
                dj = _parse(evs[j].date)
                if (dj - di).days > window_days:
                    break
                if evs[i].event_type != evs[j].event_type:
                    pair = tuple(sorted((evs[i].event_type, evs[j].event_type)))
                    pair_count[pair] += 1
                    pair_places[pair].add(place)
    out = [{"types": list(pair), "count": c, "places": sorted(pair_places[pair])}
           for pair, c in pair_count.most_common() if c >= min_count]
    return out


# --------------------------------------------------------------------------- #
# coordinated (multi-location, same-day) activity
# --------------------------------------------------------------------------- #
def coordinated_days(events: Iterable[ConflictEvent], *, scope: str = "location",
                     min_locations: int = 3) -> list[dict]:
    """Days on which activity appears across many distinct places at once.

    A broad, simultaneous flare (several sectors lighting up the same day) reads
    differently from one busy hotspot — it can indicate a coordinated push or a
    widening front. Returns days with >= ``min_locations`` distinct active places,
    busiest first.
    """
    def key(e):
        if scope == "country":
            return e.country or "(unknown)"
        if scope == "region":
            return e.region or e.country or "(unknown)"
        return _place(e)

    by_day: dict[str, set] = defaultdict(set)
    by_day_ev: Counter = Counter()
    by_day_fat: Counter = Counter()
    for e in events:
        if not _parse(e.date):
            continue
        by_day[e.date].add(key(e))
        by_day_ev[e.date] += 1
        by_day_fat[e.date] += e.fatalities
    out = [{"date": d, "locations": len(places), "events": by_day_ev[d],
            "fatalities": by_day_fat[d], "places": sorted(places)}
           for d, places in by_day.items() if len(places) >= min_locations]
    out.sort(key=lambda r: (r["locations"], r["events"]), reverse=True)
    return out


def summary(events, **kwargs) -> dict:
    """Compact correlation roll-up across all four analyses."""
    evs = list(events)
    cl = clusters(evs)
    net = actor_network(evs)
    co = cooccurrence(evs)
    cd = coordinated_days(evs)
    return {
        "cluster_count": len(cl),
        "clusters": cl,
        "actor_network": net,
        "cooccurrence": co,
        "coordinated_days": cd,
        "largest_cluster": cl[0] if cl else None,
    }
