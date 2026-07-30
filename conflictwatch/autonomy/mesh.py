"""mesh — beyond-line-of-sight comms & mesh-relay manager for DIL links.

The vehicle has to keep a reliable link to supported units across disconnected,
intermittent, and low-bandwidth (DIL) conditions. This module manages three things a
comms layer needs on the last mile:

  * **multi-bearer selection** — pick the best available radio bearer for a message given
    its bandwidth / latency needs and an EMCON preference for low-signature links;
  * **mesh relay routing** — route a message across a relay graph using a widest-path
    (max-bottleneck-quality) search, so traffic hops through the healthiest links and can
    reach nodes over the horizon;
  * **store-and-forward** — when no route exists, queue messages by priority and flush
    them in order once connectivity returns, so nothing is silently dropped.

This is a communications manager for the vehicle's own reliable messaging. It carries no
weapon, targeting, or engagement content. Pure stdlib, deterministic, offline.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Iterable, Optional


@dataclass
class Bearer:
    """A radio/transport bearer the vehicle can use."""
    name: str
    bandwidth_bps: float
    latency_ms: float
    available: bool = True
    signature: float = 0.5           # 0..1 detectability of using it
    max_range_km: float = 10.0

    def __post_init__(self):
        self.bandwidth_bps = max(0.0, float(self.bandwidth_bps))
        self.latency_ms = max(0.0, float(self.latency_ms))
        self.signature = 0.0 if self.signature < 0 else 1.0 if self.signature > 1 else float(self.signature)


def select_bearer(bearers: Iterable[Bearer], *, min_bandwidth: float = 0.0,
                  max_latency: float = float("inf"), range_km: float = 0.0,
                  prefer: str = "low_signature") -> Optional[Bearer]:
    """Pick the best available bearer meeting the constraints.

    ``prefer`` = ``low_signature`` (EMCON-first, default), ``bandwidth`` (throughput-first),
    or ``latency`` (responsiveness-first). Returns None if nothing qualifies.
    """
    if prefer not in ("low_signature", "bandwidth", "latency"):
        raise ValueError(f"unknown preference {prefer!r}")
    cands = [b for b in bearers if b.available
             and b.bandwidth_bps >= min_bandwidth
             and b.latency_ms <= max_latency
             and b.max_range_km >= range_km]
    if not cands:
        return None
    keys = {
        "low_signature": lambda b: (b.signature, b.latency_ms, -b.bandwidth_bps, b.name),
        "bandwidth": lambda b: (-b.bandwidth_bps, b.signature, b.latency_ms, b.name),
        "latency": lambda b: (b.latency_ms, b.signature, -b.bandwidth_bps, b.name),
    }
    return sorted(cands, key=keys[prefer])[0]


class MeshNetwork:
    """An undirected relay graph with per-link quality in [0, 1]."""

    def __init__(self):
        self._adj: dict[str, dict[str, float]] = {}

    def add_link(self, a: str, b: str, quality: float) -> None:
        """Add/update a bidirectional link with quality 0..1 (0 = down)."""
        q = 0.0 if quality < 0 else 1.0 if quality > 1 else float(quality)
        self._adj.setdefault(a, {})[b] = q
        self._adj.setdefault(b, {})[a] = q

    def drop_link(self, a: str, b: str) -> None:
        self._adj.get(a, {}).pop(b, None)
        self._adj.get(b, {}).pop(a, None)

    def nodes(self) -> list:
        return sorted(self._adj)

    def route(self, src: str, dst: str, min_quality: float = 0.0) -> Optional[dict]:
        """Widest-path route: maximise the weakest link along the path.

        Returns ``{path, bottleneck, hops}`` or None if no path clears ``min_quality``.
        Deterministic: ties break on lexicographically smaller next hop.
        """
        if src not in self._adj or dst not in self._adj:
            return None
        if src == dst:
            return {"path": [src], "bottleneck": 1.0, "hops": 0}
        # max-heap on bottleneck quality (negate for heapq)
        best = {src: 1.0}
        came: dict[str, str] = {}
        heap = [(-1.0, src)]
        visited = set()
        while heap:
            negq, u = heapq.heappop(heap)
            q = -negq
            if u in visited:
                continue
            visited.add(u)
            if u == dst:
                break
            for v in sorted(self._adj[u]):
                link = self._adj[u][v]
                if link <= min_quality:
                    continue
                cand = min(q, link)
                if cand > best.get(v, 0.0) + 1e-12:
                    best[v] = cand
                    came[v] = u
                    heapq.heappush(heap, (-cand, v))
        if dst not in best or best[dst] <= min_quality and min_quality > 0:
            return None
        if dst not in came and dst != src:
            return None
        # reconstruct
        path = [dst]
        while path[-1] != src:
            path.append(came[path[-1]])
        path.reverse()
        return {"path": path, "bottleneck": round(best[dst], 4), "hops": len(path) - 1}

    def reachable(self, src: str, min_quality: float = 0.0) -> set:
        """Set of nodes reachable from src over links above ``min_quality``."""
        seen = {src}
        stack = [src]
        while stack:
            u = stack.pop()
            for v, q in self._adj.get(u, {}).items():
                if q > min_quality and v not in seen:
                    seen.add(v)
                    stack.append(v)
        return seen


# ---------------------------------------------------------------------------
# Store-and-forward for disconnected/intermittent links.
# ---------------------------------------------------------------------------
_PRIORITY = {"flash": 0, "immediate": 1, "priority": 2, "routine": 3}


@dataclass(order=True)
class _Q:
    sort_key: tuple
    msg: dict = field(compare=False)


class StoreForward:
    """Priority store-and-forward queue that flushes when a route is available."""

    def __init__(self):
        self._heap: list = []
        self._seq = 0
        self.delivered: list[dict] = []

    def enqueue(self, dst: str, body, priority: str = "routine",
                t: float = 0.0) -> None:
        if priority not in _PRIORITY:
            raise ValueError(f"unknown priority {priority!r}")
        self._seq += 1
        msg = {"dst": dst, "body": body, "priority": priority, "t": t, "seq": self._seq}
        heapq.heappush(self._heap, _Q((_PRIORITY[priority], t, self._seq), msg))

    def pending(self) -> int:
        return len(self._heap)

    def flush(self, can_reach) -> list:
        """Deliver every queued message whose ``dst`` is currently reachable.

        ``can_reach(dst) -> bool`` is supplied by the caller (e.g. a `MeshNetwork`).
        Higher-priority, earlier messages leave first; undeliverable ones stay queued.
        """
        out = []
        deferred: list = []
        while self._heap:
            item = heapq.heappop(self._heap)
            if can_reach(item.msg["dst"]):
                self.delivered.append(item.msg)
                out.append(item.msg)
            else:
                deferred.append(item)
        for d in deferred:
            heapq.heappush(self._heap, d)
        return out
