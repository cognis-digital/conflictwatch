"""merge — near-duplicate detection and provenance-preserving merge across sources.

`events.dedupe` removes *exact* id collisions. But the same incident is reported many
times — ACLED, a GDELT machine-code, three wire stories — with slightly different wording,
rounded casualty counts, and jittered coordinates. Those are not exact-id matches, yet they
are one event. `merge` finds those near-duplicates and folds each group into a single
canonical `ConflictEvent` that keeps *all* the provenance.

How a match is decided (all deterministic, stdlib only):

  * **same window**   — dates within ``max_day_gap`` days (undated events never match)
  * **same place**    — either within ``radius_km`` by haversine (when both geolocated),
                        or the same country + a matching place/region token
  * **same story**    — Jaccard token-set similarity of notes+actors >= ``sim_threshold``,
                        OR an identical event type in the same place+window

The canonical record takes the earliest date, the **max** reported fatalities (most
sources under-count early), the most complete value for every other field, and a merged
provenance trail (every source, every URL, a ``merged:N`` tag and the contributing ids).

Descriptive consolidation of *reported* events for cleaner awareness and analysis — it
merges records, it does not resolve identities or fuse to a targeting picture. Offline.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date

from conflictwatch.events import ConflictEvent
from conflictwatch.correlate import haversine_km

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP = frozenset((
    "the", "a", "an", "of", "in", "on", "at", "to", "and", "or", "for", "with", "by",
    "from", "near", "was", "were", "are", "is", "as", "that", "this", "it", "its",
    "least", "up", "about", "around", "some", "after", "amid", "over", "into",
))


def _parse(d: str):
    try:
        return date.fromisoformat(d)
    except (ValueError, TypeError):
        return None


def tokenize(text: str) -> set:
    """Lowercase alphanumeric tokens with stopwords and 1-char noise removed."""
    return {t for t in _TOKEN_RE.findall((text or "").lower())
            if len(t) > 1 and t not in _STOP}


def jaccard(a: set, b: set) -> float:
    """Jaccard set similarity |A∩B| / |A∪B| (0.0 when both empty)."""
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _event_tokens(e: ConflictEvent) -> set:
    return tokenize(" ".join((e.notes or "", e.actor1 or "", e.actor2 or "",
                              e.location or "", e.region or "")))


def text_similarity(e1: ConflictEvent, e2: ConflictEvent) -> float:
    """Jaccard similarity of two events' notes+actors+place token sets."""
    return jaccard(_event_tokens(e1), _event_tokens(e2))


def _same_place(e1: ConflictEvent, e2: ConflictEvent, radius_km: float) -> bool:
    if None not in (e1.lat, e1.lon, e2.lat, e2.lon):
        return haversine_km(e1.lat, e1.lon, e2.lat, e2.lon) <= radius_km
    if e1.country and e2.country and e1.country.lower() != e2.country.lower():
        return False
    p1 = (e1.location or e1.region or "").lower()
    p2 = (e2.location or e2.region or "").lower()
    if p1 and p2:
        return p1 == p2 or p1 in p2 or p2 in p1
    # same country, at least one place unknown -> allow (window+text will gate)
    return bool(e1.country) and e1.country.lower() == (e2.country or "").lower()


def is_duplicate(e1: ConflictEvent, e2: ConflictEvent, *, max_day_gap: int = 1,
                 radius_km: float = 15.0, sim_threshold: float = 0.5) -> bool:
    """True when two events are the *same reported incident* seen from two sources.

    Requires a date on both, dates within ``max_day_gap``, the same place
    (geo-radius or country+place token), and either strong text similarity or an
    identical event type in that same place+window. Deterministic and symmetric.
    """
    d1, d2 = _parse(e1.date), _parse(e2.date)
    if not d1 or not d2:
        return False
    if abs((d1 - d2).days) > max_day_gap:
        return False
    if not _same_place(e1, e2, radius_km):
        return False
    if text_similarity(e1, e2) >= sim_threshold:
        return True
    # fall back: identical, specific event type co-located in-window reads as the same
    return (e1.event_type == e2.event_type and e1.event_type != "other")


def find_duplicates(events, *, max_day_gap: int = 1, radius_km: float = 15.0,
                    sim_threshold: float = 0.5) -> list[list[int]]:
    """Group indices of near-duplicate events (single-link union-find).

    Returns a list of index-groups covering *every* input event (singletons included),
    each group sorted ascending and the groups ordered by their smallest index — fully
    deterministic regardless of input order.
    """
    evs = list(events)
    n = len(evs)
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

    for i in range(n):
        for j in range(i + 1, n):
            if is_duplicate(evs[i], evs[j], max_day_gap=max_day_gap,
                            radius_km=radius_km, sim_threshold=sim_threshold):
                union(i, j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return [sorted(g) for _, g in sorted(groups.items())]


def _best(values, key):
    """Most complete value: prefer non-empty; among those pick by ``key`` (e.g. length)."""
    non_empty = [v for v in values if v not in (None, "", [])]
    if not non_empty:
        return values[0] if values else ""
    return max(non_empty, key=key)


def merge_events(group) -> ConflictEvent:
    """Fold a group of duplicate events into one canonical, provenance-rich record.

    Rules: **earliest** date, **max** reported fatalities, most-complete text/geo fields,
    merged sources & URLs, union of tags plus a ``merged:N`` marker and the contributing
    source ids. A single-event group is returned essentially unchanged (still tagged so
    downstream knows it passed through merge). Deterministic.
    """
    evs = list(group)
    if len(evs) == 1:
        e = evs[0]
        tags = sorted(set(e.tags) | {"merged:1"})
        return ConflictEvent(
            date=e.date, event_type=e.event_type, actor1=e.actor1, actor2=e.actor2,
            country=e.country, region=e.region, location=e.location, lat=e.lat, lon=e.lon,
            fatalities=e.fatalities, source=e.source, source_url=e.source_url,
            notes=e.notes, tags=tags)

    dated = [e.date for e in evs if _parse(e.date)]
    date_ = min(dated) if dated else evs[0].date
    fatalities = max((e.fatalities for e in evs), default=0)
    # canonical event type = most specific majority (ignore generic "other" if any specific)
    types = Counter(e.event_type for e in evs)
    specific = [t for t in types if t != "other"]
    event_type = types.most_common(1)[0][0]
    if event_type == "other" and specific:
        event_type = Counter(t for e in evs for t in [e.event_type] if t != "other").most_common(1)[0][0]

    lat = _best([e.lat for e in evs], key=lambda v: 1)
    lon = _best([e.lon for e in evs], key=lambda v: 1)
    sources = sorted({e.source for e in evs if e.source})
    urls = sorted({e.source_url for e in evs if e.source_url})
    tags = set()
    for e in evs:
        tags |= set(e.tags)
    tags.add(f"merged:{len(evs)}")
    for e in evs:
        if e.id:
            tags.add(f"src:{e.id}")

    return ConflictEvent(
        date=date_, event_type=event_type,
        actor1=_best([e.actor1 for e in evs], key=len),
        actor2=_best([e.actor2 for e in evs], key=len),
        country=_best([e.country for e in evs], key=len),
        region=_best([e.region for e in evs], key=len),
        location=_best([e.location for e in evs], key=len),
        lat=lat, lon=lon, fatalities=fatalities,
        source=" | ".join(sources), source_url=urls[0] if urls else "",
        notes=_best([e.notes for e in evs], key=len),
        tags=sorted(tags))


def merge(events, *, max_day_gap: int = 1, radius_km: float = 15.0,
          sim_threshold: float = 0.5):
    """Consolidate near-duplicate events -> ``(merged_events, report)``.

    ``merged_events`` is the deduplicated canonical list (order stable by first-seen
    index). ``report`` summarizes the operation::

        {input, output, removed, groups_merged, largest_group,
         clusters:[{canonical_id, size, source_ids, sources}]}
    """
    evs = list(events)
    groups = find_duplicates(evs, max_day_gap=max_day_gap, radius_km=radius_km,
                             sim_threshold=sim_threshold)
    merged, clusters = [], []
    for g in groups:
        members = [evs[i] for i in g]
        canon = merge_events(members)
        merged.append(canon)
        if len(members) > 1:
            clusters.append({
                "canonical_id": canon.id,
                "size": len(members),
                "source_ids": sorted(e.id for e in members),
                "sources": sorted({e.source for e in members if e.source}),
            })
    report = {
        "input": len(evs),
        "output": len(merged),
        "removed": len(evs) - len(merged),
        "groups_merged": len(clusters),
        "largest_group": max((c["size"] for c in clusters), default=1),
        "clusters": sorted(clusters, key=lambda c: (-c["size"], c["canonical_id"])),
    }
    return merged, report


def dedupe_fuzzy(events, **kwargs) -> list[ConflictEvent]:
    """Convenience: return only the merged canonical events (drops the report)."""
    merged, _ = merge(events, **kwargs)
    return merged
