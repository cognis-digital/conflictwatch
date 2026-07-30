"""dedupstore — an online, incremental near-duplicate store for streaming events.

`merge.merge` is a batch operation: it wants the whole list in memory, is O(n²) across every
pair, and produces a fresh canonical set each call. Live OSINT collection is the opposite —
events arrive one at a time from feeds and you need to know *immediately* whether each new
report is a fresh incident or another retelling of one you already hold, then fold it in.

`DedupStore` does exactly that on top of the same match rule (`merge.is_duplicate`) and the
same canonical fold (`merge.merge_events`):

  * **blocking** — candidates are indexed by ``(country, date-day-bucket)`` so a new event is
    only compared against the handful in its own space+time neighborhood, not the whole store
  * **incremental fold** — a match re-folds the matched cluster's members plus the newcomer
    into one refreshed canonical record (earliest date, max fatalities, merged provenance)
  * **stable identity** — each incident keeps a ``cluster_id`` for the life of the store so
    downstream consumers can follow an incident as new reports thicken it

Descriptive consolidation of *reported* events for live awareness — it merges retellings, it
does not resolve identities or build a targeting picture. Pure standard library, deterministic
for a given arrival order, offline.
"""

from __future__ import annotations

from datetime import date, timedelta

from conflictwatch.events import ConflictEvent
from conflictwatch.merge import is_duplicate, merge_events


def _parse(d: str):
    try:
        return date.fromisoformat(d)
    except (ValueError, TypeError):
        return None


class DedupStore:
    """An incremental near-duplicate store: :meth:`add` events, get back the canonical.

    Configured with the same knobs as :func:`conflictwatch.merge.is_duplicate`
    (``max_day_gap``, ``radius_km``, ``sim_threshold``). Internally each incident is a
    cluster with a stable integer ``cluster_id``; :meth:`add` returns a result dict telling
    you whether the newcomer opened a new incident or thickened an existing one.
    """

    def __init__(self, *, max_day_gap: int = 1, radius_km: float = 15.0,
                 sim_threshold: float = 0.5):
        self.max_day_gap = max_day_gap
        self.radius_km = radius_km
        self.sim_threshold = sim_threshold
        self._clusters: dict[int, list[ConflictEvent]] = {}
        self._canon: dict[int, ConflictEvent] = {}
        self._next_id = 0
        # blocking index: (country_lower, day_ordinal_bucket) -> set(cluster_id)
        self._blocks: dict[tuple, set] = {}

    # ------------------------------------------------------------------ #
    def _block_keys(self, e: ConflictEvent):
        """Candidate block keys an event could match — its day bucket ± the gap window."""
        country = (e.country or "").lower()
        d = _parse(e.date)
        if d is None:
            # undated events can never match (is_duplicate requires dates) -> own space
            return [(country, None)]
        base = d.toordinal()
        return [(country, base + off)
                for off in range(-self.max_day_gap, self.max_day_gap + 1)]

    def _index(self, cid: int, e: ConflictEvent):
        d = _parse(e.date)
        key = ((e.country or "").lower(), d.toordinal() if d else None)
        self._blocks.setdefault(key, set()).add(cid)

    def _reindex(self, cid: int):
        # drop stale keys then re-add for the (possibly changed) canonical
        for key, cids in list(self._blocks.items()):
            cids.discard(cid)
            if not cids:
                del self._blocks[key]
        self._index(cid, self._canon[cid])

    def _candidates(self, e: ConflictEvent) -> list[int]:
        out: set = set()
        for key in self._block_keys(e):
            out |= self._blocks.get(key, set())
        return sorted(out)

    # ------------------------------------------------------------------ #
    def add(self, event: ConflictEvent) -> dict:
        """Ingest one event; fold it into a matching incident or open a new one.

        Returns ``{cluster_id, is_new, size, canonical}`` where ``is_new`` is True when the
        event started a fresh incident. When it matched, the matched cluster's canonical is
        refreshed to include the newcomer's provenance and higher counts. Deterministic for
        a fixed arrival order; on a tie the lowest ``cluster_id`` wins.
        """
        for cid in self._candidates(event):
            if is_duplicate(self._canon[cid], event, max_day_gap=self.max_day_gap,
                            radius_km=self.radius_km, sim_threshold=self.sim_threshold):
                self._clusters[cid].append(event)
                self._canon[cid] = merge_events(self._clusters[cid])
                self._reindex(cid)
                return {"cluster_id": cid, "is_new": False,
                        "size": len(self._clusters[cid]), "canonical": self._canon[cid]}
        cid = self._next_id
        self._next_id += 1
        self._clusters[cid] = [event]
        self._canon[cid] = merge_events([event])
        self._index(cid, self._canon[cid])
        return {"cluster_id": cid, "is_new": True, "size": 1,
                "canonical": self._canon[cid]}

    def add_many(self, events) -> list[dict]:
        """Ingest an iterable of events in order; return the per-event result dicts."""
        return [self.add(e) for e in events]

    # ------------------------------------------------------------------ #
    def canonical_events(self) -> list[ConflictEvent]:
        """The current deduplicated canonical records, ordered by first-seen cluster id."""
        return [self._canon[cid] for cid in sorted(self._canon)]

    def cluster(self, cluster_id: int) -> list[ConflictEvent]:
        """The raw member events folded into one incident (as ingested)."""
        return list(self._clusters.get(cluster_id, ()))

    def __len__(self) -> int:
        """Number of distinct incidents currently held."""
        return len(self._clusters)

    def size(self, cluster_id: int) -> int:
        """How many raw reports back a given incident."""
        return len(self._clusters.get(cluster_id, ()))

    def duplicates(self) -> int:
        """Total reports folded away (sum of cluster sizes minus incident count)."""
        return sum(len(m) for m in self._clusters.values()) - len(self._clusters)

    def report(self) -> dict:
        """A batch-style rollup mirroring :func:`conflictwatch.merge.merge`'s report.

        ``{input, output, removed, groups_merged, largest_group, clusters:[...]}`` — where
        ``clusters`` lists every multi-report incident, largest first.
        """
        total_in = sum(len(m) for m in self._clusters.values())
        multi = []
        for cid in sorted(self._clusters):
            members = self._clusters[cid]
            if len(members) > 1:
                multi.append({
                    "cluster_id": cid,
                    "canonical_id": self._canon[cid].id,
                    "size": len(members),
                    "source_ids": sorted(e.id for e in members),
                    "sources": sorted({e.source for e in members if e.source}),
                })
        multi.sort(key=lambda c: (-c["size"], c["canonical_id"]))
        return {
            "input": total_in,
            "output": len(self._clusters),
            "removed": total_in - len(self._clusters),
            "groups_merged": len(multi),
            "largest_group": max((c["size"] for c in multi), default=1),
            "clusters": multi,
        }


def dedup_stream(events, *, max_day_gap: int = 1, radius_km: float = 15.0,
                 sim_threshold: float = 0.5):
    """Feed an iterable through a fresh :class:`DedupStore` -> ``(canonical, report)``.

    A streaming analogue of :func:`conflictwatch.merge.merge`: identical inputs of course
    can differ from the batch result at the edges (single-link vs. canonical-anchored
    matching), but the incident set is stable for a fixed arrival order.
    """
    store = DedupStore(max_day_gap=max_day_gap, radius_km=radius_km,
                       sim_threshold=sim_threshold)
    store.add_many(events)
    return store.canonical_events(), store.report()
