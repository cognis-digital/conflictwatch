"""Tests for conflictwatch.actorflux — temporal dynamics of the actor
co-occurrence network. All deterministic and offline; events are built
in-memory. Covers windowing, actor trajectories & momentum, emerging/fading
actors, tie formation/dissolution, structural-trend reads, serialization, the
module-level convenience helpers, and parametrized property sweeps."""

from __future__ import annotations

import json
import random

import pytest

from conflictwatch import actorflux
from conflictwatch.actorflux import FluxTimeline, WindowSnapshot
from conflictwatch.events import ConflictEvent


def _ev(**kw):
    kw.setdefault("country", "X")
    kw.setdefault("event_type", "battle")
    return ConflictEvent(**kw)


def _chain():
    """A small deterministic timeline: 3 weekly windows, a moving front."""
    return [
        _ev(date="2026-01-01", actor1="A", actor2="B"),
        _ev(date="2026-01-02", actor1="A", actor2="C"),
        _ev(date="2026-01-10", actor1="A", actor2="B"),
        _ev(date="2026-01-11", actor1="B", actor2="D"),
        _ev(date="2026-01-20", actor1="D", actor2="E"),
    ]


# --- module surface ----------------------------------------------------------
def test_module_registered_on_package():
    import conflictwatch
    assert conflictwatch.actorflux is actorflux
    assert "actorflux" in conflictwatch.__all__


def test_build_returns_timeline():
    assert isinstance(actorflux.build(_chain()), FluxTimeline)


# --- windowing ---------------------------------------------------------------
def test_empty_events_empty_timeline():
    tl = actorflux.build([])
    assert tl.window_count == 0
    assert tl.span() is None
    assert tl.labels() == []
    assert tl.actors() == []


def test_window_count_and_span():
    tl = actorflux.build(_chain(), window_days=7)
    assert tl.window_count == 3
    assert tl.span() == {"start": "2026-01-01", "end": "2026-01-21"}


def test_window_positions_are_reindexed_contiguously():
    tl = actorflux.build(_chain(), window_days=7)
    assert [w.position for w in tl.windows] == [0, 1, 2]


def test_only_nonempty_windows_kept():
    # a 30-day gap would create empty buckets; they must not appear
    evs = [_ev(date="2026-01-01", actor1="A", actor2="B"),
           _ev(date="2026-03-01", actor1="C", actor2="D")]
    tl = actorflux.build(evs, window_days=7)
    assert tl.window_count == 2  # not the ~9 calendar weeks between them


def test_window_bounds_span_window_days():
    tl = actorflux.build(_chain(), window_days=7)
    w0 = tl.windows[0]
    assert w0.start == "2026-01-01" and w0.end == "2026-01-07"


def test_larger_window_merges_events():
    tl = actorflux.build(_chain(), window_days=30)
    assert tl.window_count == 1
    assert tl.windows[0].event_count == 5


def test_undated_events_counted_not_placed():
    evs = _chain() + [_ev(actor1="Z", actor2="Q")]
    tl = actorflux.build(evs, window_days=7)
    assert tl.undated == 1
    assert "Z" not in tl.actors()


def test_all_undated_gives_empty_timeline_with_count():
    tl = actorflux.build([_ev(actor1="Z", actor2="Q")])
    assert tl.window_count == 0 and tl.undated == 1


def test_unparseable_date_is_undated():
    tl = actorflux.build([_ev(date="not-a-date", actor1="A", actor2="B")])
    # events._iso_date keeps the leading 10 chars; a non-ISO value stays undated
    assert tl.window_count == 0 and tl.undated == 1


@pytest.mark.parametrize("wd", [0, -1, -7])
def test_invalid_window_days_raises(wd):
    with pytest.raises(ValueError):
        actorflux.build(_chain(), window_days=wd)


@pytest.mark.parametrize("wd", [1, 2, 3, 5, 7, 14, 30, 90])
def test_window_days_recorded(wd):
    tl = actorflux.build(_chain(), window_days=wd)
    assert tl.window_days == wd


@pytest.mark.parametrize("wd", [1, 2, 3, 7, 14, 30])
def test_event_counts_conserved_across_windows(wd):
    tl = actorflux.build(_chain(), window_days=wd)
    assert sum(w.event_count for w in tl.windows) == 5


# --- WindowSnapshot ----------------------------------------------------------
def test_snapshot_structure_stats_keys():
    tl = actorflux.build(_chain(), window_days=7)
    s = tl.windows[0].stats()
    assert set(s) == {"position", "start", "end", "event_count",
                      "order", "size", "density", "component_count"}


def test_density_zero_for_single_actor_window():
    w = WindowSnapshot(0, "2026-01-01", "2026-01-07", 1,
                       actorflux.ActorGraph.from_events(
                           [_ev(date="2026-01-01", actor1="Solo")]))
    assert w.density == 0.0


def test_density_one_for_single_pair():
    tl = actorflux.build([_ev(date="2026-01-01", actor1="A", actor2="B")])
    assert tl.windows[0].density == 1.0


@pytest.mark.parametrize("wd", [1, 7, 30])
def test_density_within_unit_interval(wd):
    tl = actorflux.build(_chain(), window_days=wd)
    for w in tl.windows:
        assert 0.0 <= w.density <= 1.0


def test_events_for_absent_actor_is_zero():
    tl = actorflux.build(_chain(), window_days=7)
    assert tl.windows[2].events_for("A") == 0


def test_events_for_present_actor():
    tl = actorflux.build(_chain(), window_days=7)
    # A appears in both events of window 0
    assert tl.windows[0].events_for("A") == 2


# --- trajectories ------------------------------------------------------------
def test_trajectory_keys():
    tl = actorflux.build(_chain(), window_days=7)
    t = tl.actor_trajectory("A")
    assert set(t) == {"actor", "degree", "strength", "events", "present",
                      "first_window", "last_window", "peak_window",
                      "peak_degree", "span", "delta", "slope", "trend"}


def test_trajectory_series_length_matches_windows():
    tl = actorflux.build(_chain(), window_days=7)
    t = tl.actor_trajectory("A")
    n = tl.window_count
    assert len(t["degree"]) == len(t["strength"]) == len(t["present"]) == n


def test_trajectory_absent_actor_all_zero():
    tl = actorflux.build(_chain(), window_days=7)
    t = tl.actor_trajectory("Nobody")
    assert t["present"] == [False, False, False]
    assert t["degree"] == [0, 0, 0]
    assert t["first_window"] == -1 and t["last_window"] == -1
    assert t["trend"] == "steady"


def test_trajectory_first_last_peak():
    tl = actorflux.build(_chain(), window_days=7)
    t = tl.actor_trajectory("A")
    assert t["first_window"] == 0
    assert t["last_window"] == 1
    assert t["peak_window"] == 0 and t["peak_degree"] == 2
    assert t["span"] == 2


def test_trajectory_falling_actor():
    tl = actorflux.build(_chain(), window_days=7)
    t = tl.actor_trajectory("A")  # degree [2,1,0]
    assert t["trend"] == "falling" and t["slope"] < 0
    assert t["delta"] == -1


def test_trajectory_rising_actor():
    tl = actorflux.build(_chain(), window_days=7)
    t = tl.actor_trajectory("D")  # degree [0,1,1]
    assert t["trend"] == "rising" and t["slope"] > 0


def test_trajectories_cover_all_actors_sorted():
    tl = actorflux.build(_chain(), window_days=7)
    names = [t["actor"] for t in tl.trajectories()]
    assert names == sorted(names)
    assert set(names) == set(tl.actors())


@pytest.mark.parametrize("actor", ["A", "B", "C", "D", "E"])
def test_trajectory_present_iff_degree_or_isolated(actor):
    tl = actorflux.build(_chain(), window_days=7)
    t = tl.actor_trajectory(actor)
    # every window where the actor is present must be inside [first,last]
    for i, p in enumerate(t["present"]):
        if p:
            assert t["first_window"] <= i <= t["last_window"]


# --- rising / fading ---------------------------------------------------------
def test_rising_only_positive_slopes():
    tl = actorflux.build(_chain(), window_days=7)
    assert all(r["slope"] > 0 for r in tl.rising_actors())


def test_fading_only_negative_slopes():
    tl = actorflux.build(_chain(), window_days=7)
    assert all(r["slope"] < 0 for r in tl.fading_actors())


def test_rising_sorted_desc():
    tl = actorflux.build(_chain(), window_days=7)
    slopes = [r["slope"] for r in tl.rising_actors()]
    assert slopes == sorted(slopes, reverse=True)


def test_rising_top_truncates():
    tl = actorflux.build(_chain(), window_days=7)
    assert len(tl.rising_actors(top=1)) <= 1


def test_rising_top_zero_empty():
    tl = actorflux.build(_chain(), window_days=7)
    assert tl.rising_actors(top=0) == []


def test_A_is_fading_and_D_is_rising():
    tl = actorflux.build(_chain(), window_days=7)
    assert "A" in [r["actor"] for r in tl.fading_actors()]
    assert "D" in [r["actor"] for r in tl.rising_actors()]


def test_rising_fading_disjoint():
    tl = actorflux.build(_chain(), window_days=7)
    up = {r["actor"] for r in tl.rising_actors()}
    down = {r["actor"] for r in tl.fading_actors()}
    assert up.isdisjoint(down)


# --- emerging / departed -----------------------------------------------------
def test_emerging_actor_is_E():
    tl = actorflux.build(_chain(), window_days=7)
    assert [r["actor"] for r in tl.emerging_actors()] == ["E"]


def test_emerging_within_widens_set():
    tl = actorflux.build(_chain(), window_days=7)
    wide = {r["actor"] for r in tl.emerging_actors(within=2)}
    assert {"D", "E"} <= wide


def test_departed_actor_includes_C():
    tl = actorflux.build(_chain(), window_days=7)
    # C only appears in window 0, absent from the final window
    assert "C" in [r["actor"] for r in tl.departed_actors()]


def test_emerging_sorted_by_degree_then_name():
    tl = actorflux.build(_chain(), window_days=7)
    rows = tl.emerging_actors(within=3)
    keys = [(-r["degree"], r["actor"]) for r in rows]
    assert keys == sorted(keys)


@pytest.mark.parametrize("within", [0, -1])
def test_emerging_bad_within_raises(within):
    tl = actorflux.build(_chain(), window_days=7)
    with pytest.raises(ValueError):
        tl.emerging_actors(within=within)
    with pytest.raises(ValueError):
        tl.departed_actors(within=within)


def test_emerging_empty_timeline():
    tl = actorflux.build([])
    assert tl.emerging_actors() == []
    assert tl.departed_actors() == []


def test_single_window_all_emerging_none_departed():
    tl = actorflux.build(_chain(), window_days=90)
    assert tl.window_count == 1
    assert {r["actor"] for r in tl.emerging_actors()} == set(tl.actors())
    assert tl.departed_actors() == []


# --- tie dynamics ------------------------------------------------------------
def test_tie_changes_boundary_count():
    tl = actorflux.build(_chain(), window_days=7)
    assert len(tl.tie_changes()) == tl.window_count - 1


def test_tie_changes_formed_and_dropped():
    tl = actorflux.build(_chain(), window_days=7)
    tc0 = tl.tie_changes()[0]
    assert tc0["formed"] == [["B", "D"]]
    assert tc0["dropped"] == [["A", "C"]]
    assert tc0["formed_count"] == 1 and tc0["dropped_count"] == 1


def test_tie_pairs_are_sorted_source_lt_target():
    tl = actorflux.build(_chain(), window_days=7)
    for tc in tl.tie_changes():
        for pair in tc["formed"] + tc["dropped"]:
            assert pair[0] < pair[1]


def test_emerging_ties_final_boundary():
    tl = actorflux.build(_chain(), window_days=7)
    assert tl.emerging_ties() == [["D", "E"]]


def test_persistent_ties_none_in_chain():
    tl = actorflux.build(_chain(), window_days=7)
    assert tl.persistent_ties() == []


def test_persistent_tie_present_every_window():
    evs = [
        _ev(date="2026-01-01", actor1="A", actor2="B"),
        _ev(date="2026-01-10", actor1="A", actor2="B"),
        _ev(date="2026-01-20", actor1="A", actor2="B"),
    ]
    tl = actorflux.build(evs, window_days=7)
    assert tl.persistent_ties() == [["A", "B"]]


def test_single_window_no_tie_changes():
    tl = actorflux.build(_chain(), window_days=90)
    assert tl.tie_changes() == []
    assert tl.emerging_ties() == []


def test_empty_timeline_ties():
    tl = actorflux.build([])
    assert tl.persistent_ties() == []
    assert tl.emerging_ties() == []
    assert tl.tie_changes() == []


# --- structural trend --------------------------------------------------------
def test_structural_trend_keys():
    tl = actorflux.build(_chain(), window_days=7)
    st = tl.structural_trend()
    assert set(st) == {"trend", "density_slope", "density", "components",
                       "mean_density", "mean_components"}


def test_structural_trend_empty():
    assert actorflux.build([]).structural_trend()["trend"] == "empty"


def test_structural_trend_single():
    assert actorflux.build(_chain(), window_days=90).structural_trend()["trend"] == "single"


def test_structural_trend_consolidating():
    tl = actorflux.build(_chain(), window_days=7)  # density 0.67 -> 0.67 -> 1.0
    assert tl.structural_trend()["trend"] == "consolidating"


def test_structural_trend_fragmenting():
    # start dense (triangle), end sparse (isolated pair) -> density falls
    evs = [
        _ev(date="2026-01-01", actor1="A", actor2="B"),
        _ev(date="2026-01-01", actor1="B", actor2="C"),
        _ev(date="2026-01-01", actor1="A", actor2="C"),
        _ev(date="2026-01-20", actor1="D", actor2="E"),
        _ev(date="2026-01-20", actor1="F", actor2="G"),
        _ev(date="2026-01-20", actor1="H", actor2="I"),
    ]
    tl = actorflux.build(evs, window_days=7)
    assert tl.structural_trend()["trend"] == "fragmenting"


def test_density_and_component_series_lengths():
    tl = actorflux.build(_chain(), window_days=7)
    assert len(tl.density_series()) == tl.window_count
    assert len(tl.component_series()) == tl.window_count


# --- serialization -----------------------------------------------------------
def test_to_dict_is_json_serializable():
    tl = actorflux.build(_chain(), window_days=7)
    s = json.dumps(tl.to_dict())
    assert isinstance(s, str) and len(s) > 0


def test_to_dict_keys():
    tl = actorflux.build(_chain(), window_days=7)
    d = tl.to_dict()
    assert set(d) == {"window_days", "window_count", "span", "undated",
                      "actor_count", "structure", "rising", "fading",
                      "emerging", "departed", "tie_changes", "persistent_ties",
                      "emerging_ties", "structural_trend"}


def test_to_dict_empty_timeline():
    d = actorflux.build([]).to_dict()
    assert d["window_count"] == 0 and d["span"] is None


# --- convenience functions ---------------------------------------------------
def test_timeline_helper_matches_to_dict():
    evs = _chain()
    assert actorflux.timeline(evs, window_days=7) == \
        actorflux.build(evs, window_days=7).to_dict(top=10)


def test_rising_helper():
    evs = _chain()
    assert actorflux.rising(evs, window_days=7) == \
        actorflux.build(evs, window_days=7).rising_actors(top=10)


def test_emerging_helper():
    evs = _chain()
    assert actorflux.emerging(evs, window_days=7) == \
        actorflux.build(evs, window_days=7).emerging_actors(within=1)


def test_summary_helper_is_timeline():
    evs = _chain()
    assert actorflux.summary(evs, window_days=7, top=5) == \
        actorflux.timeline(evs, window_days=7, top=5)


# --- low-level helpers -------------------------------------------------------
@pytest.mark.parametrize("ys,expect_sign", [
    ([0, 1, 2, 3], 1),
    ([3, 2, 1, 0], -1),
    ([1, 1, 1, 1], 0),
    ([5], 0),
    ([], 0),
])
def test_slope_sign(ys, expect_sign):
    s = actorflux._slope([float(y) for y in ys])
    assert (s > 0) - (s < 0) == expect_sign


@pytest.mark.parametrize("slope,label", [
    (1.0, "rising"), (-1.0, "falling"), (0.0, "steady"),
    (1e-12, "steady"), (-1e-12, "steady"),
])
def test_trend_label(slope, label):
    assert actorflux._trend(slope) == label


@pytest.mark.parametrize("s,ok", [
    ("2026-01-01", True), ("2026-01-01T12:00:00", True),
    ("", False), ("bad", False), ("2026-13-40", False),
])
def test_event_day_parse(s, ok):
    d = actorflux._event_day(_ev(date=s, actor1="A", actor2="B"))
    assert (d is not None) == ok


# --- determinism property sweeps ---------------------------------------------
def _random_events(seed, n=40):
    rng = random.Random(seed)
    actors = list("ABCDEFGH")
    out = []
    for _ in range(n):
        day = rng.randint(1, 28)
        month = rng.randint(1, 3)
        a, b = rng.sample(actors, 2)
        out.append(_ev(date=f"2026-{month:02d}-{day:02d}", actor1=a, actor2=b,
                       fatalities=rng.randint(0, 4)))
    return out


@pytest.mark.parametrize("seed", range(8))
def test_shuffle_invariant_to_dict(seed):
    evs = _random_events(seed)
    a = actorflux.build(evs, window_days=7).to_dict()
    shuffled = list(evs)
    random.Random(seed + 100).shuffle(shuffled)
    b = actorflux.build(shuffled, window_days=7).to_dict()
    assert a == b


@pytest.mark.parametrize("seed", range(6))
def test_repeated_build_identical(seed):
    evs = _random_events(seed)
    assert actorflux.timeline(evs) == actorflux.timeline(evs)


@pytest.mark.parametrize("seed,wd", [(s, wd) for s in range(4) for wd in (1, 7, 30)])
def test_event_conservation_property(seed, wd):
    evs = _random_events(seed)
    tl = actorflux.build(evs, window_days=wd)
    assert sum(w.event_count for w in tl.windows) + tl.undated == len(evs)


@pytest.mark.parametrize("seed", range(6))
def test_windows_chronological(seed):
    tl = actorflux.build(_random_events(seed), window_days=7)
    starts = tl.labels()
    assert starts == sorted(starts)


@pytest.mark.parametrize("seed", range(6))
def test_density_bounds_property(seed):
    tl = actorflux.build(_random_events(seed), window_days=7)
    assert all(0.0 <= d <= 1.0 for d in tl.density_series())


@pytest.mark.parametrize("seed", range(6))
def test_emerging_first_window_in_range(seed):
    tl = actorflux.build(_random_events(seed), window_days=7)
    n = tl.window_count
    for r in tl.emerging_actors(within=1):
        assert r["first_window"] == n - 1


@pytest.mark.parametrize("seed", range(6))
def test_persistent_ties_are_subset_of_every_window(seed):
    tl = actorflux.build(_random_events(seed), window_days=7)
    persistent = {tuple(p) for p in tl.persistent_ties()}
    for w in tl.windows:
        edges = {(e["source"], e["target"]) for e in w.graph.edges()}
        assert persistent <= edges


@pytest.mark.parametrize("seed", range(6))
def test_actor_count_matches_union(seed):
    tl = actorflux.build(_random_events(seed), window_days=7)
    union = set()
    for w in tl.windows:
        union.update(w.actors())
    assert tl.to_dict()["actor_count"] == len(union)


@pytest.mark.parametrize("seed", range(6))
def test_rising_fading_disjoint_property(seed):
    tl = actorflux.build(_random_events(seed), window_days=7)
    up = {r["actor"] for r in tl.rising_actors()}
    down = {r["actor"] for r in tl.fading_actors()}
    assert up.isdisjoint(down)
