"""actorgraph — the actor co-occurrence network and who sits at its center.

`correlate.actor_network` gives you the raw belligerent graph (nodes + weighted
edges). This module asks the next question an analyst asks of any network:
**who is central?** — which actors are the connectors, the hubs, the brokers that
tie otherwise-separate parts of the conflict together.

It builds a weighted, undirected **co-occurrence graph** from a stream of normalized
``ConflictEvent`` records (two actors share an edge when they appear on the same
reported event) and ranks the actors with a family of classic, deterministic
centrality measures computed from pure standard library:

  * **degree**            — how many distinct other actors an actor co-occurs with.
  * **strength**          — weighted degree: total co-occurrence weight incident.
  * **degree centrality** — degree normalized to ``[0, 1]`` by ``(n - 1)``.
  * **betweenness**       — how often an actor lies on shortest paths between others
                            (Brandes' algorithm; a *broker* / connector score).
  * **closeness**         — inverse mean shortest-path distance to reachable actors
                            (Wasserman–Faust normalized for disconnected graphs).
  * **eigenvector**       — influence weighted by the influence of one's neighbors
                            (power iteration).
  * **pagerank**          — random-walk prominence with damping.
  * **coreness (k-core)** — the deepest k-core an actor survives in (cohesion).
  * **components**        — connected sub-networks (distinct theaters of actors).
  * **communities**       — deterministic label-propagation groupings.

Everything is descriptive open-source network analysis for **situational
awareness** — it summarizes the structure of *reported* co-occurrences for a human
to read. It does not target, task collection, nominate, or recommend force. Pure
standard library, deterministic (stable tie-breaking, sorted iteration), offline.
"""

from __future__ import annotations

import heapq
import math
from collections import Counter, defaultdict, deque
from typing import Iterable, Optional

from conflictwatch.events import ConflictEvent

# metric keys understood by ``rank`` / ``ActorGraph.ranking``
METRICS = ("degree", "strength", "degree_centrality", "betweenness",
           "closeness", "eigenvector", "pagerank", "coreness")


def _actors(e: ConflictEvent) -> list[str]:
    """Distinct, stripped, non-blank actor names on one event (actor1/actor2)."""
    seen: list[str] = []
    for a in (e.actor1, e.actor2):
        if a and a.strip():
            v = a.strip()
            if v not in seen:
                seen.append(v)
    return seen


class ActorGraph:
    """A weighted, undirected actor co-occurrence graph with centrality measures.

    Construct with :func:`build` (or ``ActorGraph.from_events``). All actor
    iteration is over ``self.actors`` (sorted), so every measure is deterministic
    regardless of the order events arrived in. Isolated actors (those that never
    co-occur with anyone) are retained as zero-degree nodes.
    """

    def __init__(self) -> None:
        self._adj: dict[str, dict[str, int]] = defaultdict(dict)   # actor -> {nbr: weight}
        self._node_events: Counter = Counter()                     # events actor appears in
        self._node_fat: Counter = Counter()                        # fatalities on those events
        self._edge_fat: dict[tuple, int] = defaultdict(int)        # (a<b) -> fatalities
        self._actors: list[str] = []                               # cached sorted node list
        self._dirty = True

    # ------------------------------------------------------------------ build
    @classmethod
    def from_events(cls, events: Iterable[ConflictEvent]) -> "ActorGraph":
        g = cls()
        for e in events:
            acts = _actors(e)
            fat = int(e.fatalities or 0)
            for a in acts:
                g._node_events[a] += 1
                g._node_fat[a] += fat
                g._adj.setdefault(a, {})
            for i in range(len(acts)):
                for j in range(i + 1, len(acts)):
                    a, b = acts[i], acts[j]
                    g._adj[a][b] = g._adj[a].get(b, 0) + 1
                    g._adj[b][a] = g._adj[b].get(a, 0) + 1
                    g._edge_fat[tuple(sorted((a, b)))] += fat
        g._dirty = True
        return g

    def _refresh(self) -> None:
        if self._dirty:
            self._actors = sorted(self._adj.keys())
            self._dirty = False

    # ------------------------------------------------------------ structure
    @property
    def actors(self) -> list[str]:
        """All actors (nodes), sorted lexicographically. Stable, deterministic."""
        self._refresh()
        return list(self._actors)

    @property
    def order(self) -> int:
        """Number of actors (nodes)."""
        self._refresh()
        return len(self._actors)

    @property
    def size(self) -> int:
        """Number of distinct undirected edges."""
        self._refresh()
        return sum(len(nbrs) for nbrs in self._adj.values()) // 2

    def has_actor(self, a: str) -> bool:
        return a in self._adj

    def neighbors(self, a: str) -> dict[str, int]:
        """``{neighbor: weight}`` for one actor (empty if isolated/unknown)."""
        return dict(self._adj.get(a, {}))

    def edges(self) -> list[dict]:
        """Undirected edges ``{source, target, weight, fatalities}``.

        Sorted heaviest-first, then by (source, target). Each pair appears once
        with ``source < target``.
        """
        self._refresh()
        out = []
        for a in self._actors:
            for b, w in self._adj[a].items():
                if a < b:
                    out.append({"source": a, "target": b, "weight": w,
                                "fatalities": self._edge_fat[(a, b)]})
        out.sort(key=lambda e: (-e["weight"], e["source"], e["target"]))
        return out

    def node_stats(self) -> list[dict]:
        """Per-actor ``{actor, events, fatalities, degree, strength}`` list.

        Sorted by degree desc, then strength desc, then actor name.
        """
        self._refresh()
        out = []
        for a in self._actors:
            nbrs = self._adj[a]
            out.append({
                "actor": a,
                "events": self._node_events[a],
                "fatalities": self._node_fat[a],
                "degree": len(nbrs),
                "strength": sum(nbrs.values()),
            })
        out.sort(key=lambda d: (-d["degree"], -d["strength"], d["actor"]))
        return out

    # -------------------------------------------------------------- degree
    def degree(self, a: str) -> int:
        """Number of distinct actors ``a`` co-occurs with."""
        return len(self._adj.get(a, {}))

    def strength(self, a: str) -> int:
        """Weighted degree: total co-occurrence weight incident on ``a``."""
        return sum(self._adj.get(a, {}).values())

    def degrees(self) -> dict[str, int]:
        return {a: self.degree(a) for a in self.actors}

    def strengths(self) -> dict[str, int]:
        return {a: self.strength(a) for a in self.actors}

    def degree_centrality(self) -> dict[str, float]:
        """Degree normalized by ``(n - 1)`` into ``[0, 1]`` (0 for n<=1)."""
        self._refresh()
        n = len(self._actors)
        if n <= 1:
            return {a: 0.0 for a in self._actors}
        return {a: len(self._adj[a]) / (n - 1) for a in self._actors}

    # ---------------------------------------------------------- components
    def components(self) -> list[list[str]]:
        """Connected components as sorted actor lists.

        Returned largest-first, ties broken by the component's first actor.
        Partitions every actor exactly once.
        """
        self._refresh()
        seen: set = set()
        comps: list[list[str]] = []
        for start in self._actors:
            if start in seen:
                continue
            stack, comp = [start], []
            seen.add(start)
            while stack:
                u = stack.pop()
                comp.append(u)
                for v in self._adj[u]:
                    if v not in seen:
                        seen.add(v)
                        stack.append(v)
            comps.append(sorted(comp))
        comps.sort(key=lambda c: (-len(c), c[0]))
        return comps

    # ---------------------------------------------------- shortest paths
    def _bfs_dist(self, src: str) -> dict[str, int]:
        """Unweighted shortest-path hop counts from ``src`` (BFS)."""
        dist = {src: 0}
        q = deque([src])
        while q:
            u = q.popleft()
            for v in self._adj[u]:
                if v not in dist:
                    dist[v] = dist[u] + 1
                    q.append(v)
        return dist

    def _dijkstra_dist(self, src: str) -> dict[str, float]:
        """Weighted shortest-path distances from ``src``.

        Edge length is ``1 / weight`` — a heavier co-occurrence tie is a
        *shorter* distance (actors more tightly bound are "closer").
        """
        dist: dict[str, float] = {src: 0.0}
        pq: list[tuple] = [(0.0, src)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist.get(u, math.inf):
                continue
            for v, w in self._adj[u].items():
                nd = d + 1.0 / w
                if nd < dist.get(v, math.inf):
                    dist[v] = nd
                    heapq.heappush(pq, (nd, v))
        return dist

    def closeness_centrality(self, *, weighted: bool = False) -> dict[str, float]:
        """Closeness centrality, Wasserman–Faust normalized for disconnection.

        For each actor, ``C = (r / (n-1)) * ((r) / sum_dist)`` where ``r`` is the
        number of *other* reachable actors and ``sum_dist`` the total distance to
        them. Isolated actors score 0. Values lie in ``[0, 1]`` for the unweighted
        case. Deterministic.
        """
        self._refresh()
        n = len(self._actors)
        out = {a: 0.0 for a in self._actors}
        if n <= 1:
            return out
        for a in self._actors:
            dist = (self._dijkstra_dist(a) if weighted else self._bfs_dist(a))
            reach = [d for v, d in dist.items() if v != a]
            total = sum(reach)
            r = len(reach)
            if r > 0 and total > 0:
                out[a] = (r / total) * (r / (n - 1))
        return out

    def betweenness_centrality(self, *, weighted: bool = False,
                               normalized: bool = True) -> dict[str, float]:
        """Betweenness centrality via Brandes' algorithm (undirected).

        Counts, for each actor, the fraction of shortest paths between all other
        pairs that pass through it — a *broker* / connector score. Uses BFS
        (unweighted) or Dijkstra (weighted, length = 1/weight). When
        ``normalized`` the raw score is divided by ``(n-1)(n-2)`` (undirected
        halving already applied), giving ``[0, 1]``. Deterministic: source order
        is sorted and the endpoint contribution is excluded.
        """
        self._refresh()
        nodes = self._actors
        n = len(nodes)
        bc = {a: 0.0 for a in nodes}
        if n <= 2:
            return bc
        for s in nodes:
            stack: list[str] = []
            pred: dict[str, list[str]] = {v: [] for v in nodes}
            sigma = dict.fromkeys(nodes, 0.0)
            sigma[s] = 1.0
            if weighted:
                dist: dict[str, float] = {s: 0.0}
                seen = {s: 0.0}
                pq: list[tuple] = [(0.0, s)]
                done: set = set()
                while pq:
                    d, v = heapq.heappop(pq)
                    if v in done:
                        continue
                    done.add(v)
                    stack.append(v)
                    for w_, wt in sorted(self._adj[v].items()):
                        nd = d + 1.0 / wt
                        if w_ not in seen or nd < seen[w_] - 1e-12:
                            seen[w_] = nd
                            dist[w_] = nd
                            sigma[w_] = sigma[v]
                            pred[w_] = [v]
                            heapq.heappush(pq, (nd, w_))
                        elif abs(nd - seen[w_]) <= 1e-12:
                            sigma[w_] += sigma[v]
                            pred[w_].append(v)
            else:
                dist = {s: 0}
                q = deque([s])
                while q:
                    v = q.popleft()
                    stack.append(v)
                    for w_ in self._adj[v]:
                        if w_ not in dist:
                            dist[w_] = dist[v] + 1
                            q.append(w_)
                        if dist[w_] == dist[v] + 1:
                            sigma[w_] += sigma[v]
                            pred[w_].append(v)
            delta = dict.fromkeys(nodes, 0.0)
            while stack:
                w_ = stack.pop()
                for v in pred[w_]:
                    if sigma[w_] > 0:
                        delta[v] += (sigma[v] / sigma[w_]) * (1.0 + delta[w_])
                if w_ != s:
                    bc[w_] += delta[w_]
        # undirected: each shortest path counted twice
        for v in bc:
            bc[v] /= 2.0
        if normalized:
            scale = (n - 1) * (n - 2) / 2.0
            if scale > 0:
                for v in bc:
                    bc[v] /= scale
        return bc

    # ---------------------------------------------------- eigen / pagerank
    def eigenvector_centrality(self, *, max_iter: int = 200,
                               tol: float = 1e-9) -> dict[str, float]:
        """Eigenvector centrality by power iteration on the weighted adjacency.

        An actor is central if its neighbors are central. The result is
        L2-normalized and non-negative. Converges on any finite graph; on an
        empty/edgeless graph returns a uniform normalized vector. Deterministic
        (uniform start, sorted iteration).
        """
        self._refresh()
        nodes = self._actors
        n = len(nodes)
        if n == 0:
            return {}
        x = {a: 1.0 / math.sqrt(n) for a in nodes}
        for _ in range(max_iter):
            nx = {a: 0.0 for a in nodes}
            for a in nodes:
                acc = 0.0
                for b, w in self._adj[a].items():
                    acc += w * x[b]
                nx[a] = acc
            norm = math.sqrt(sum(v * v for v in nx.values()))
            if norm == 0:
                # edgeless: fall back to uniform
                return {a: 1.0 / math.sqrt(n) for a in nodes}
            nx = {a: v / norm for a, v in nx.items()}
            if sum(abs(nx[a] - x[a]) for a in nodes) < tol:
                x = nx
                break
            x = nx
        # sign convention: make the dominant vector non-negative
        if sum(x.values()) < 0:
            x = {a: -v for a, v in x.items()}
        return x

    def pagerank(self, *, damping: float = 0.85, max_iter: int = 200,
                 tol: float = 1e-9) -> dict[str, float]:
        """Weighted PageRank (undirected walk) summing to 1.

        Random-walk prominence: probability a walker that follows co-occurrence
        edges (with teleport ``1 - damping``) is at each actor in the stationary
        distribution. Dangling (isolated) actors redistribute uniformly.
        Deterministic; values are non-negative and sum to ~1.
        """
        self._refresh()
        nodes = self._actors
        n = len(nodes)
        if n == 0:
            return {}
        pr = {a: 1.0 / n for a in nodes}
        out_w = {a: float(sum(self._adj[a].values())) for a in nodes}
        base = (1.0 - damping) / n
        for _ in range(max_iter):
            dangling = damping * sum(pr[a] for a in nodes if out_w[a] == 0) / n
            nxt = {a: base + dangling for a in nodes}
            for a in nodes:
                if out_w[a] == 0:
                    continue
                share = damping * pr[a] / out_w[a]
                for b, w in self._adj[a].items():
                    nxt[b] += share * w
            diff = sum(abs(nxt[a] - pr[a]) for a in nodes)
            pr = nxt
            if diff < tol:
                break
        # renormalize against float drift
        total = sum(pr.values())
        if total > 0:
            pr = {a: v / total for a, v in pr.items()}
        return pr

    # --------------------------------------------------------- k-core / coreness
    def coreness(self) -> dict[str, int]:
        """k-core number of each actor via iterative min-degree peeling.

        The coreness of an actor is the largest ``k`` such that it belongs to a
        subgraph where every actor has degree >= ``k``. A cohesion measure:
        actors deep in a dense core score high; isolated actors score ``0``.
        Deterministic — the lowest-degree actor (ties by name) is peeled next,
        and its removal decrements surviving neighbors, the standard
        Batagelj–Zaveršnik result. ``O(V^2)`` here (fine for OSINT-scale graphs).
        """
        self._refresh()
        d = {a: len(self._adj[a]) for a in self._actors}
        core: dict[str, int] = {}
        processed: set = set()
        remaining = list(self._actors)
        while remaining:
            # peel the smallest-degree survivor; name breaks ties
            v = min(remaining, key=lambda a: (d[a], a))
            processed.add(v)
            remaining.remove(v)
            core[v] = d[v]
            for u in self._adj[v]:
                if u not in processed and d[u] > d[v]:
                    d[u] -= 1
        return core

    # ------------------------------------------------------------- communities
    def communities(self, *, max_iter: int = 100) -> list[list[str]]:
        """Deterministic label-propagation communities.

        Each actor adopts the label carrying the greatest incident edge weight
        among its neighbors (ties broken by smallest label), iterating over
        actors in sorted order until labels stabilize or ``max_iter``. Returns
        communities as sorted actor lists, largest-first. Isolated actors form
        singleton communities.
        """
        self._refresh()
        nodes = self._actors
        label = {a: a for a in nodes}
        for _ in range(max_iter):
            changed = False
            for a in nodes:
                nbrs = self._adj[a]
                if not nbrs:
                    continue
                tally: dict[str, int] = defaultdict(int)
                for b, w in nbrs.items():
                    tally[label[b]] += w
                best = min(tally.items(), key=lambda kv: (-kv[1], kv[0]))[0]
                if label[a] != best:
                    label[a] = best
                    changed = True
            if not changed:
                break
        groups: dict[str, list[str]] = defaultdict(list)
        for a in nodes:
            groups[label[a]].append(a)
        comms = [sorted(members) for members in groups.values()]
        comms.sort(key=lambda c: (-len(c), c[0]))
        return comms

    # ---------------------------------------------------------------- ranking
    def centrality(self, *, weighted: bool = False,
                   include: Optional[Iterable[str]] = None) -> dict[str, dict]:
        """Every centrality measure keyed by actor.

        Returns ``{actor: {degree, strength, degree_centrality, betweenness,
        closeness, eigenvector, pagerank, coreness}}``. ``weighted`` switches
        betweenness/closeness to distance = 1/weight.
        """
        self._refresh()
        want = set(include) if include is not None else set(METRICS)
        dc = self.degree_centrality() if "degree_centrality" in want else {}
        bc = self.betweenness_centrality(weighted=weighted) if "betweenness" in want else {}
        cc = self.closeness_centrality(weighted=weighted) if "closeness" in want else {}
        ev = self.eigenvector_centrality() if "eigenvector" in want else {}
        pr = self.pagerank() if "pagerank" in want else {}
        core = self.coreness() if "coreness" in want else {}
        out = {}
        for a in self._actors:
            row = {}
            if "degree" in want:
                row["degree"] = self.degree(a)
            if "strength" in want:
                row["strength"] = self.strength(a)
            if "degree_centrality" in want:
                row["degree_centrality"] = dc[a]
            if "betweenness" in want:
                row["betweenness"] = bc[a]
            if "closeness" in want:
                row["closeness"] = cc[a]
            if "eigenvector" in want:
                row["eigenvector"] = ev[a]
            if "pagerank" in want:
                row["pagerank"] = pr[a]
            if "coreness" in want:
                row["coreness"] = core[a]
            out[a] = row
        return out

    def ranking(self, *, metric: str = "degree", top: Optional[int] = None,
                weighted: bool = False) -> list[dict]:
        """Actors ranked by one centrality ``metric``, highest-first.

        Ties are broken by actor name (ascending) for stable, deterministic
        output. Each row is ``{actor, <metric>, events, fatalities}``. ``top``
        truncates to the leading N. Raises ``ValueError`` on an unknown metric.
        """
        if metric not in METRICS:
            raise ValueError(f"unknown metric {metric!r}; choose from {METRICS}")
        self._refresh()
        if metric == "degree":
            score = self.degrees()
        elif metric == "strength":
            score = self.strengths()
        elif metric == "degree_centrality":
            score = self.degree_centrality()
        elif metric == "betweenness":
            score = self.betweenness_centrality(weighted=weighted)
        elif metric == "closeness":
            score = self.closeness_centrality(weighted=weighted)
        elif metric == "eigenvector":
            score = self.eigenvector_centrality()
        elif metric == "pagerank":
            score = self.pagerank()
        else:  # coreness
            score = self.coreness()
        rows = [{"actor": a, metric: score[a],
                 "events": self._node_events[a], "fatalities": self._node_fat[a]}
                for a in self._actors]
        rows.sort(key=lambda r: (-r[metric], r["actor"]))
        if top is not None:
            rows = rows[:max(0, top)]
        return rows

    # ------------------------------------------------------------------- dict
    def to_dict(self, *, weighted: bool = False) -> dict:
        """Serializable snapshot: nodes (with centrality), edges, components."""
        cen = self.centrality(weighted=weighted)
        nodes = []
        for row in self.node_stats():
            a = row["actor"]
            merged = dict(row)
            merged.update(cen[a])
            nodes.append(merged)
        return {
            "order": self.order,
            "size": self.size,
            "nodes": nodes,
            "edges": self.edges(),
            "components": self.components(),
        }


# --------------------------------------------------------------------------- #
# module-level convenience
# --------------------------------------------------------------------------- #
def build(events: Iterable[ConflictEvent]) -> ActorGraph:
    """Build an :class:`ActorGraph` from a stream of ConflictEvents."""
    return ActorGraph.from_events(events)


def rank(events: Iterable[ConflictEvent], *, metric: str = "degree",
         top: Optional[int] = None, weighted: bool = False) -> list[dict]:
    """One-shot: build the graph and return the ranking for ``metric``."""
    return build(events).ranking(metric=metric, top=top, weighted=weighted)


def central_actors(events: Iterable[ConflictEvent], *, top: int = 10,
                   weighted: bool = False) -> dict:
    """Compact multi-metric leaderboard for situational awareness.

    Returns ``{metric: [rows...]}`` for degree, betweenness, eigenvector and
    pagerank — the four an analyst reaches for first (hubs, brokers, influence,
    prominence) — each truncated to ``top``.
    """
    g = build(events)
    return {
        "degree": g.ranking(metric="degree", top=top),
        "betweenness": g.ranking(metric="betweenness", top=top, weighted=weighted),
        "eigenvector": g.ranking(metric="eigenvector", top=top),
        "pagerank": g.ranking(metric="pagerank", top=top),
    }


def summary(events: Iterable[ConflictEvent], *, top: int = 10,
            weighted: bool = False) -> dict:
    """Full situational-awareness roll-up of the actor co-occurrence network.

    Structure + leaders + groupings in one deterministic dict::

        {order, size, component_count, components, community_count, communities,
         leaders:{degree,betweenness,eigenvector,pagerank}, nodes, edges}
    """
    g = build(events)
    comps = g.components()
    comms = g.communities()
    return {
        "order": g.order,
        "size": g.size,
        "component_count": len(comps),
        "components": comps,
        "community_count": len(comms),
        "communities": comms,
        "leaders": central_actors(events, top=top, weighted=weighted),
        "nodes": g.node_stats(),
        "edges": g.edges(),
    }
