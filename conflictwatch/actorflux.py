"""actorflux — temporal dynamics of the actor co-occurrence network.

:mod:`~conflictwatch.actorgraph` answers "who is central *now*" over the whole
event stream at once. ``actorflux`` adds the **time axis** an analyst reaches
for next: *how is the network changing?* — which actors are **rising** or
**fading**, which co-occurrence **ties are newly forming or going quiet**, and
whether the overall structure is **consolidating** or **fragmenting** across a
sequence of dated time windows.

It slices a stream of normalized ``ConflictEvent`` records into consecutive
calendar windows (``window_days`` apart, anchored on the earliest dated event),
builds one :class:`~conflictwatch.actorgraph.ActorGraph` per window, and layers
change-over-time descriptors on top:

  * **structure series**   — order / size / density / component-count per window.
  * **actor trajectories**  — an actor's degree, strength and event counts across
                              windows, with a least-squares **momentum** slope
                              and a ``rising`` / ``falling`` / ``steady`` label.
  * **emerging actors**     — actors that first appear only in the latest window(s).
  * **fading actors**       — actors present earlier but absent from the latest window.
  * **tie dynamics**        — co-occurrence edges **formed** and **dropped** at
                              each window boundary, plus the **persistent** ties
                              present in every window.
  * **structural trend**    — a consolidating / fragmenting / stable read on the
                              density and component trajectory.

Everything is descriptive open-source network analysis for **situational
awareness** — it summarizes how the structure of *reported* co-occurrences
shifts over time for a human to read. It does not target, task collection,
nominate actors, identify gaps to exploit, or recommend force. Pure standard
library, deterministic (stable date ordering, sorted iteration, fixed
tie-breaking), offline.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Iterable, Optional

from conflictwatch.actorgraph import ActorGraph
from conflictwatch.events import ConflictEvent

# how much slope counts as a real trend rather than float noise
_TREND_EPS = 1e-9
# density swing (0..1) that separates a consolidating/fragmenting read from stable
_STRUCT_EPS = 0.05


def _event_day(e: ConflictEvent) -> Optional[date]:
    """The event's calendar day as a ``date``, or ``None`` if undated/unparseable.

    ``ConflictEvent.date`` is already normalized to ``YYYY-MM-DD`` (or blank),
    so we only need a lenient ISO parse of the leading 10 characters.
    """
    s = (e.date or "").strip()
    if len(s) < 10:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _slope(ys: list[float]) -> float:
    """Least-squares slope of ``ys`` over evenly spaced x = 0, 1, ... , n-1.

    Deterministic, closed-form (no iteration). Returns ``0.0`` for fewer than
    two points or a degenerate (constant-x) fit. Positive means the series is
    trending up across the window sequence.
    """
    n = len(ys)
    if n < 2:
        return 0.0
    mx = (n - 1) / 2.0
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in enumerate(ys))
    den = sum((x - mx) ** 2 for x in range(n))
    return num / den if den else 0.0


def _trend(slope: float) -> str:
    """Label a momentum slope as ``rising`` / ``falling`` / ``steady``."""
    if slope > _TREND_EPS:
        return "rising"
    if slope < -_TREND_EPS:
        return "falling"
    return "steady"


def _edge_set(g: ActorGraph) -> set:
    """Set of undirected ties ``(source, target)`` with ``source < target``."""
    return {(e["source"], e["target"]) for e in g.edges()}


class WindowSnapshot:
    """One time window's actor co-occurrence graph plus its calendar bounds.

    ``position`` is the window's 0-based index within the (non-empty) timeline,
    while ``start`` / ``end`` are the ISO calendar bounds of the window bucket.
    ``graph`` is a fully-built :class:`ActorGraph` for the events in the window.
    """

    __slots__ = ("position", "start", "end", "event_count", "graph", "_evmap")

    def __init__(self, position: int, start: str, end: str,
                 event_count: int, graph: ActorGraph) -> None:
        self.position = position
        self.start = start
        self.end = end
        self.event_count = event_count
        self.graph = graph
        self._evmap: Optional[dict] = None

    def events_for(self, actor: str) -> int:
        """Number of window events ``actor`` appears in (0 if absent).

        Derived once from the graph's public ``node_stats`` and cached, so
        repeated trajectory queries stay cheap.
        """
        if self._evmap is None:
            self._evmap = {r["actor"]: r["events"] for r in self.graph.node_stats()}
        return self._evmap.get(actor, 0)

    @property
    def order(self) -> int:
        """Number of distinct actors in the window."""
        return self.graph.order

    @property
    def size(self) -> int:
        """Number of distinct co-occurrence ties in the window."""
        return self.graph.size

    @property
    def density(self) -> float:
        """Edge density in ``[0, 1]``: ties / possible ties (0 for order<=1)."""
        n = self.graph.order
        if n <= 1:
            return 0.0
        return self.graph.size / (n * (n - 1) / 2.0)

    @property
    def component_count(self) -> int:
        """Number of connected sub-networks in the window."""
        return len(self.graph.components())

    def actors(self) -> list[str]:
        """Sorted actors present in this window."""
        return self.graph.actors

    def stats(self) -> dict:
        """Serializable per-window structure summary."""
        return {
            "position": self.position,
            "start": self.start,
            "end": self.end,
            "event_count": self.event_count,
            "order": self.order,
            "size": self.size,
            "density": round(self.density, 6),
            "component_count": self.component_count,
        }


class FluxTimeline:
    """An ordered sequence of :class:`WindowSnapshot` co-occurrence graphs.

    Build with :func:`build`. Every accessor iterates windows in chronological
    order and actors in sorted order, so all output is deterministic regardless
    of the order events arrived in. ``undated`` counts events dropped from the
    timeline because they carried no parseable date (they still exist, they just
    cannot be placed on the time axis).
    """

    def __init__(self, windows: list[WindowSnapshot], *, window_days: int,
                 undated: int = 0) -> None:
        self.windows = windows
        self.window_days = window_days
        self.undated = undated

    # ---------------------------------------------------------------- structure
    @property
    def window_count(self) -> int:
        """Number of non-empty windows on the timeline."""
        return len(self.windows)

    def span(self) -> Optional[dict]:
        """``{start, end}`` calendar bounds of the timeline, or ``None`` if empty."""
        if not self.windows:
            return None
        return {"start": self.windows[0].start, "end": self.windows[-1].end}

    def labels(self) -> list[str]:
        """Window start dates, in order — a compact x-axis for the series."""
        return [w.start for w in self.windows]

    def structure(self) -> list[dict]:
        """Per-window structure summary rows, in chronological order."""
        return [w.stats() for w in self.windows]

    def actors(self) -> list[str]:
        """All actors ever seen across the timeline, sorted lexicographically."""
        seen: set = set()
        for w in self.windows:
            seen.update(w.actors())
        return sorted(seen)

    # -------------------------------------------------------------- trajectory
    def actor_trajectory(self, actor: str) -> dict:
        """Degree / strength / event series for one actor across all windows.

        Returns a dict with per-window lists (``degree``, ``strength``,
        ``events``, ``present``) plus derived fields: ``first_window`` /
        ``last_window`` (positions where the actor is present, or ``-1``),
        ``peak_window`` / ``peak_degree``, ``span`` (windows between first and
        last appearance, inclusive), ``delta`` (last-present minus first-present
        degree), ``slope`` (least-squares momentum over the *full* window
        sequence, treating absence as 0) and a ``trend`` label. Deterministic.
        """
        degree: list[int] = []
        strength: list[int] = []
        events: list[int] = []
        present: list[bool] = []
        for w in self.windows:
            g = w.graph
            here = g.has_actor(actor)
            present.append(here)
            degree.append(g.degree(actor) if here else 0)
            strength.append(g.strength(actor) if here else 0)
            events.append(w.events_for(actor) if here else 0)
        idx_present = [i for i, p in enumerate(present) if p]
        first = idx_present[0] if idx_present else -1
        last = idx_present[-1] if idx_present else -1
        if idx_present:
            peak = max(idx_present, key=lambda i: (degree[i], i))
            peak_degree = degree[peak]
            delta = degree[last] - degree[first]
            span = last - first + 1
        else:
            peak, peak_degree, delta, span = -1, 0, 0, 0
        slope = _slope([float(d) for d in degree])
        return {
            "actor": actor,
            "degree": degree,
            "strength": strength,
            "events": events,
            "present": present,
            "first_window": first,
            "last_window": last,
            "peak_window": peak,
            "peak_degree": peak_degree,
            "span": span,
            "delta": delta,
            "slope": round(slope, 6),
            "trend": _trend(slope),
        }

    def trajectories(self) -> list[dict]:
        """:meth:`actor_trajectory` for every actor, sorted by actor name."""
        return [self.actor_trajectory(a) for a in self.actors()]

    def rising_actors(self, *, top: Optional[int] = None) -> list[dict]:
        """Actors with the strongest positive degree momentum, highest-first.

        Only actors with a positive slope are returned. Ties break by later
        last-appearance (more current), then by actor name. Each row is
        ``{actor, slope, delta, first_window, last_window, degree}``.
        """
        rows = []
        for a in self.actors():
            t = self.actor_trajectory(a)
            if t["slope"] > _TREND_EPS:
                rows.append({
                    "actor": a,
                    "slope": t["slope"],
                    "delta": t["delta"],
                    "first_window": t["first_window"],
                    "last_window": t["last_window"],
                    "degree": t["degree"],
                })
        rows.sort(key=lambda r: (-r["slope"], -r["last_window"], r["actor"]))
        if top is not None:
            rows = rows[:max(0, top)]
        return rows

    def fading_actors(self, *, top: Optional[int] = None) -> list[dict]:
        """Actors with the strongest negative degree momentum, steepest-first.

        Only actors with a negative slope are returned. Ties break by earlier
        last-appearance (gone longer), then by actor name.
        """
        rows = []
        for a in self.actors():
            t = self.actor_trajectory(a)
            if t["slope"] < -_TREND_EPS:
                rows.append({
                    "actor": a,
                    "slope": t["slope"],
                    "delta": t["delta"],
                    "first_window": t["first_window"],
                    "last_window": t["last_window"],
                    "degree": t["degree"],
                })
        rows.sort(key=lambda r: (r["slope"], r["last_window"], r["actor"]))
        if top is not None:
            rows = rows[:max(0, top)]
        return rows

    def emerging_actors(self, *, within: int = 1) -> list[dict]:
        """Actors whose *first* appearance is within the last ``within`` windows.

        These are the new names entering the reported network. ``within`` must
        be >= 1; with a single window every actor is "emerging". Rows are
        ``{actor, first_window, degree}`` (degree in that first window), sorted
        by degree desc then actor name.
        """
        if within < 1:
            raise ValueError("within must be >= 1")
        n = self.window_count
        if n == 0:
            return []
        threshold = n - within
        rows = []
        for a in self.actors():
            t = self.actor_trajectory(a)
            fw = t["first_window"]
            if fw >= threshold:
                rows.append({
                    "actor": a,
                    "first_window": fw,
                    "degree": t["degree"][fw],
                })
        rows.sort(key=lambda r: (-r["degree"], r["actor"]))
        return rows

    def departed_actors(self, *, within: int = 1) -> list[dict]:
        """Actors present earlier but absent from the last ``within`` windows.

        The counterpart to :meth:`emerging_actors`: names that have dropped out
        of the reported network. Rows are ``{actor, last_window, degree}``
        (degree in that last-present window), sorted by degree desc then name.
        """
        if within < 1:
            raise ValueError("within must be >= 1")
        n = self.window_count
        if n == 0:
            return []
        threshold = n - within
        rows = []
        for a in self.actors():
            t = self.actor_trajectory(a)
            lw = t["last_window"]
            if 0 <= lw < threshold:
                rows.append({
                    "actor": a,
                    "last_window": lw,
                    "degree": t["degree"][lw],
                })
        rows.sort(key=lambda r: (-r["degree"], r["actor"]))
        return rows

    # -------------------------------------------------------------- tie change
    def tie_changes(self) -> list[dict]:
        """Co-occurrence ties **formed** and **dropped** at each window boundary.

        For every adjacent window pair ``(i, i+1)`` returns a row
        ``{from_window, to_window, formed, dropped, formed_count,
        dropped_count}`` where ``formed`` are ties present in ``i+1`` but not in
        ``i`` and ``dropped`` the reverse. Tie lists are ``[source, target]``
        pairs sorted lexicographically. Empty list when there is <2 windows.
        """
        out = []
        for i in range(len(self.windows) - 1):
            prev = _edge_set(self.windows[i].graph)
            cur = _edge_set(self.windows[i + 1].graph)
            formed = sorted(cur - prev)
            dropped = sorted(prev - cur)
            out.append({
                "from_window": i,
                "to_window": i + 1,
                "formed": [list(t) for t in formed],
                "dropped": [list(t) for t in dropped],
                "formed_count": len(formed),
                "dropped_count": len(dropped),
            })
        return out

    def persistent_ties(self) -> list[list]:
        """Ties present in **every** window (the stable spine of the network).

        Returns ``[source, target]`` pairs sorted lexicographically. Empty when
        there are no windows.
        """
        if not self.windows:
            return []
        common = _edge_set(self.windows[0].graph)
        for w in self.windows[1:]:
            common &= _edge_set(w.graph)
        return [list(t) for t in sorted(common)]

    def emerging_ties(self) -> list[list]:
        """Ties newly formed at the final window boundary (freshest links).

        Present in the last window but absent from the second-to-last. Empty
        when there are fewer than two windows.
        """
        if len(self.windows) < 2:
            return []
        prev = _edge_set(self.windows[-2].graph)
        cur = _edge_set(self.windows[-1].graph)
        return [list(t) for t in sorted(cur - prev)]

    # --------------------------------------------------------- structural read
    def density_series(self) -> list[float]:
        """Per-window edge density in ``[0, 1]``, chronological, rounded 6dp."""
        return [round(w.density, 6) for w in self.windows]

    def component_series(self) -> list[int]:
        """Per-window connected-component count, chronological."""
        return [w.component_count for w in self.windows]

    def structural_trend(self) -> dict:
        """A consolidating / fragmenting / stable read of the network over time.

        Compares first vs last window density (a swing beyond ``_STRUCT_EPS``
        flags ``consolidating`` when rising, ``fragmenting`` when falling) and
        reports the density/component series plus their means. Returns
        ``{trend, density_slope, density, components, mean_density,
        mean_components}``; ``trend`` is ``"empty"`` for no windows and
        ``"single"`` for one.
        """
        dens = self.density_series()
        comps = self.component_series()
        if not dens:
            return {"trend": "empty", "density_slope": 0.0, "density": [],
                    "components": [], "mean_density": 0.0, "mean_components": 0.0}
        slope = round(_slope([float(d) for d in dens]), 6)
        if len(dens) == 1:
            trend = "single"
        else:
            change = dens[-1] - dens[0]
            if change > _STRUCT_EPS:
                trend = "consolidating"
            elif change < -_STRUCT_EPS:
                trend = "fragmenting"
            else:
                trend = "stable"
        return {
            "trend": trend,
            "density_slope": slope,
            "density": dens,
            "components": comps,
            "mean_density": round(sum(dens) / len(dens), 6),
            "mean_components": round(sum(comps) / len(comps), 6),
        }

    # ------------------------------------------------------------------- dict
    def to_dict(self, *, top: Optional[int] = 10) -> dict:
        """Serializable situational-awareness roll-up of the whole timeline."""
        return {
            "window_days": self.window_days,
            "window_count": self.window_count,
            "span": self.span(),
            "undated": self.undated,
            "actor_count": len(self.actors()),
            "structure": self.structure(),
            "rising": self.rising_actors(top=top),
            "fading": self.fading_actors(top=top),
            "emerging": self.emerging_actors(),
            "departed": self.departed_actors(),
            "tie_changes": self.tie_changes(),
            "persistent_ties": self.persistent_ties(),
            "emerging_ties": self.emerging_ties(),
            "structural_trend": self.structural_trend(),
        }


# --------------------------------------------------------------------------- #
# module-level convenience
# --------------------------------------------------------------------------- #
def build(events: Iterable[ConflictEvent], *, window_days: int = 7) -> FluxTimeline:
    """Slice ``events`` into consecutive windows and build a :class:`FluxTimeline`.

    Windows are ``window_days`` wide, anchored on the earliest dated event, and
    only non-empty windows are kept (positions are re-indexed 0..k-1 in calendar
    order). Events without a parseable date are counted in ``undated`` and left
    off the time axis. ``window_days`` must be >= 1. Deterministic.
    """
    if window_days < 1:
        raise ValueError("window_days must be >= 1")
    dated: list[tuple] = []
    undated = 0
    for e in events:
        d = _event_day(e)
        if d is None:
            undated += 1
        else:
            dated.append((d, e))
    if not dated:
        return FluxTimeline([], window_days=window_days, undated=undated)
    dated.sort(key=lambda t: (t[0], t[1].id))
    origin = dated[0][0]
    buckets: dict[int, list] = defaultdict(list)
    for d, e in dated:
        idx = (d - origin).days // window_days
        buckets[idx].append(e)
    windows: list[WindowSnapshot] = []
    for pos, idx in enumerate(sorted(buckets)):
        wevents = buckets[idx]
        start = origin + timedelta(days=idx * window_days)
        end = start + timedelta(days=window_days - 1)
        windows.append(WindowSnapshot(
            position=pos,
            start=start.isoformat(),
            end=end.isoformat(),
            event_count=len(wevents),
            graph=ActorGraph.from_events(wevents),
        ))
    return FluxTimeline(windows, window_days=window_days, undated=undated)


def timeline(events: Iterable[ConflictEvent], *, window_days: int = 7,
             top: Optional[int] = 10) -> dict:
    """One-shot: build the timeline and return its :meth:`FluxTimeline.to_dict`."""
    return build(events, window_days=window_days).to_dict(top=top)


def rising(events: Iterable[ConflictEvent], *, window_days: int = 7,
           top: Optional[int] = 10) -> list[dict]:
    """One-shot leaderboard of the actors gaining connectivity over time."""
    return build(events, window_days=window_days).rising_actors(top=top)


def emerging(events: Iterable[ConflictEvent], *, window_days: int = 7,
             within: int = 1) -> list[dict]:
    """One-shot list of actors newly entering the network in recent windows."""
    return build(events, window_days=window_days).emerging_actors(within=within)


def summary(events: Iterable[ConflictEvent], *, window_days: int = 7,
            top: int = 10) -> dict:
    """Full situational-awareness roll-up of the actor network's evolution.

    Alias for :func:`timeline` with a fixed ``top`` — the one call an analyst
    makes to see structure, movers, and tie churn over the reporting period.
    """
    return timeline(events, window_days=window_days, top=top)
