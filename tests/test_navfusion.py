"""Tests for conflictwatch.autonomy.navfusion — GPS-denied navigation fusion.
Deterministic, offline. Navigation-only: estimates own position, no targeting."""

from __future__ import annotations

import math

import pytest

from conflictwatch.autonomy import navfusion as nf


def test_wrap_angle():
    assert nf.wrap_angle(0.0) == 0.0
    assert math.isclose(nf.wrap_angle(3 * math.pi), math.pi)
    assert math.isclose(nf.wrap_angle(-3 * math.pi), math.pi)
    assert -math.pi < nf.wrap_angle(10.0) <= math.pi


def test_fix_clamps_variance_and_source():
    f = nf.Fix(x=1, y=2, var=-5, source="bogus")
    assert f.var > 0 and f.source == "manual"
    assert nf.Fix(x=0, y=0, var=1, source="vio").source == "vio"


def test_dead_reckon_moves_east():
    nav = nf.Navigator()
    nav.predict(speed=2.0, heading_rate=0.0, dt=5.0)
    assert math.isclose(nav.state.x, 10.0, abs_tol=1e-9)
    assert math.isclose(nav.state.y, 0.0, abs_tol=1e-9)


def test_dead_reckon_turn_moves_north():
    nav = nf.Navigator(heading=math.pi / 2)
    nav.predict(speed=1.0, heading_rate=0.0, dt=4.0)
    assert math.isclose(nav.state.y, 4.0, abs_tol=1e-9)
    assert abs(nav.state.x) < 1e-9


def test_covariance_grows_without_fix():
    nav = nf.Navigator(pos_var=1.0, process_noise=2.0)
    s0 = nav.state.pos_sigma
    nav.predict(speed=1.0, dt=3.0)
    assert nav.state.pos_sigma > s0
    assert math.isclose(nav.state.var_x, 1.0 + 2.0 * 3.0)


def test_covariance_capped():
    nav = nf.Navigator(pos_var=1.0, process_noise=1e9, max_var=100.0)
    nav.predict(speed=0.0, dt=10.0)
    assert nav.state.var_x <= 100.0


def test_update_pulls_toward_fix_and_shrinks_var():
    nav = nf.Navigator(pos_var=100.0)
    nav.predict(speed=0.0, dt=0.0)
    v0 = nav.state.var_x
    nav.update(nf.Fix(x=50.0, y=0.0, var=1.0, source="gnss"))
    assert 45.0 < nav.state.x <= 50.0     # tight fix pulls hard
    assert nav.state.var_x < v0
    assert nav.state.last_fix == "gnss" and nav.state.fixes == 1


def test_tight_fix_beats_loose_fix():
    tight = nf.Navigator(pos_var=100.0); tight.update(nf.Fix(10, 0, 0.1))
    loose = nf.Navigator(pos_var=100.0); loose.update(nf.Fix(10, 0, 1000.0))
    assert tight.state.x > loose.state.x   # tight fix moves closer to 10


def test_quality_decreases_with_uncertainty():
    good = nf.Navigator(pos_var=1.0)
    bad = nf.Navigator(pos_var=10000.0)
    assert good.quality() > bad.quality()
    # Quality is bounded in [0, 1]; the smooth curve approaches 1.0 for small
    # sigma (1 m -> ~0.99) but only reaches it at sigma == 0.
    assert 0.0 <= bad.quality() <= good.quality() <= 1.0
    assert good.quality() > 0.95   # near-perfect fix -> high quality


def test_mode_reflects_last_fix():
    nav = nf.Navigator()
    assert nav.mode() == "dead-reckoning"
    nav.update(nf.Fix(0, 0, 1, source="terrain"))
    assert nav.mode() == "terrain-relative"
    nav.update(nf.Fix(0, 0, 1, source="vio"))
    assert nav.mode() == "odometry-aided"
    nav.update(nf.Fix(0, 0, 1, source="gnss"))
    assert nav.mode() == "gnss"


def test_gnss_dropout_is_noop():
    nav = nf.Navigator()
    nav.predict(speed=1.0, dt=1.0)
    x = nav.state.x
    assert nav.gnss_dropout() is None
    assert nav.state.x == x


def test_terrainmap_requires_2x2():
    with pytest.raises(ValueError):
        nf.TerrainMap([[1.0]])


def test_terrainmap_bilinear():
    grid = [[0, 10], [0, 10]]
    tm = nf.TerrainMap(grid, cell=10.0)
    assert math.isclose(tm.elevation_at(0, 0), 0.0)
    assert math.isclose(tm.elevation_at(10, 0), 10.0)
    assert math.isclose(tm.elevation_at(5, 0), 5.0)


def test_terrainmap_clamps_out_of_bounds():
    tm = nf.TerrainMap([[0, 10], [0, 10]], cell=10.0)
    # sampling far outside clamps to edge, no crash
    assert tm.elevation_at(-100, -100) == 0.0
    assert tm.elevation_at(1000, 1000) == 10.0


def test_match_profile_finds_position():
    # elevation ramps with x: z = x/10 ; a sample of 3.0 -> x≈30
    grid = [[c for c in range(6)] for _ in range(6)]
    tm = nf.TerrainMap(grid, cell=10.0)
    fix = tm.match_profile([(0, 0, 3.0)], guess_x=20, guess_y=20, search=30)
    assert fix.source == "terrain"
    assert math.isclose(fix.x, 30.0, abs_tol=5.0)


def test_match_profile_variance_reflects_residual():
    grid = [[c for c in range(6)] for _ in range(6)]
    tm = nf.TerrainMap(grid, cell=10.0)
    good = tm.match_profile([(0, 0, 3.0)], 30, 20, search=10)
    # an impossible elevation (way off the map) yields a worse (larger) variance
    bad = tm.match_profile([(0, 0, 999.0)], 30, 20, search=10)
    assert bad.var > good.var


def test_match_profile_empty_raises():
    tm = nf.TerrainMap([[0, 1], [0, 1]], cell=10.0)
    with pytest.raises(ValueError):
        tm.match_profile([], 0, 0)


def test_dead_reckon_helper_matches_stepwise():
    st = nf.dead_reckon([(2.0, 0.0, 1.0), (2.0, 0.0, 1.0)])
    assert math.isclose(st.x, 4.0, abs_tol=1e-9)


def test_fusion_recovers_after_dropout():
    # drift out on DR, then a terrain fix reins the estimate back in
    nav = nf.Navigator(process_noise=1.0)
    for _ in range(20):
        nav.predict(speed=1.0, dt=1.0)
    drifted_sigma = nav.state.pos_sigma
    nav.update(nf.Fix(x=20.0, y=0.0, var=4.0, source="terrain"))
    assert nav.state.pos_sigma < drifted_sigma
