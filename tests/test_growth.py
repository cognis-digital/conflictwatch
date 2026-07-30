"""Tests for the dynamic dataset-growth engine and the harvest frontier."""
import pytest

from conflictwatch import catalog
from conflictwatch.frontier import ISO_3166_ALPHA2, build_frontier, parametric_sources
from conflictwatch.growth import (
    Endpoint,
    GrowthEngine,
    HarvestStore,
    Record,
    SourceStats,
    expand,
    synthetic_fetcher,
)


# ---- expand ----

def test_expand_produces_base_plus_per_term():
    eps = expand([("Src", "http://x")], ["A", "B", "C"], param_name="q")
    keys = {e.key for e in eps}
    assert "Src:_all" in keys
    assert "Src:q=A" in keys and "Src:q=C" in keys
    assert len(eps) == 4   # base + 3 terms


def test_expand_keys_unique():
    eps = expand([("S1", "http://a"), ("S2", "http://b")], ["x", "y"])
    assert len({e.key for e in eps}) == len(eps)


def test_expand_url_query_separator():
    eps = expand([("S", "http://x?a=1")], ["z"], param_name="q")
    per = [e for e in eps if e.params]
    assert per[0].url.endswith("&q=z")   # & because URL already had ?


# ---- harvest store ----

def test_store_merge_dedup():
    store = HarvestStore()
    r1 = Record("u1", "S", "e", 0.0)
    r2 = Record("u2", "S", "e", 0.0)
    assert store.merge([r1, r2]) == 2
    assert store.merge([r1]) == 0          # duplicate ignored
    assert store.size == 2


def test_store_tracks_sources():
    store = HarvestStore()
    store.merge([Record("a", "S1", "e", 0.0), Record("b", "S2", "e", 0.0),
                 Record("c", "S1", "e", 0.0)])
    assert store.sources() == {"S1": 2, "S2": 1}


def test_store_freshness():
    store = HarvestStore()
    store.merge([Record("a", "S", "e", 0.0), Record("b", "S", "e", 10.0)])
    assert store.freshness(now=20.0) == pytest.approx((20 + 10) / 2)


# ---- source stats / reliability ----

def test_reliability_zero_without_fetches():
    assert SourceStats().reliability == 0.0


def test_reliability_penalizes_errors():
    good = SourceStats(fetches=10, records=100, new_records=100, errors=0)
    bad = SourceStats(fetches=10, records=100, new_records=100, errors=8)
    assert good.reliability > bad.reliability


# ---- growth engine ----

def _engine():
    eng = GrowthEngine(fetcher=synthetic_fetcher(records_per_endpoint=3))
    eng.add_endpoints(expand([("S", "http://x")], [str(i) for i in range(10)]))
    return eng


def test_engine_grows_over_cycles():
    eng = _engine()
    reports = eng.grow(cycles=3, start=0.0, dt=1.0)
    sizes = [r.total_size for r in reports]
    # Store strictly grows each cycle (new timestamps -> new uids).
    assert sizes[0] < sizes[1] < sizes[2]


def test_engine_dedup_same_timestamp():
    eng = _engine()
    a = eng.run_cycle(now=5.0)
    b = eng.run_cycle(now=5.0)          # same time -> same uids -> nothing new
    assert a.new_records > 0
    assert b.new_records == 0
    assert b.total_size == a.total_size


def test_engine_frontier_expands_with_discovery():
    # Discoverer spawns one new endpoint per cycle from the first record.
    def discover(records):
        r = records[0]
        return [Endpoint("D", f"disc:{r.uid}", "http://d")]
    eng = GrowthEngine(fetcher=synthetic_fetcher(2), discoverer=discover)
    eng.add_endpoints([Endpoint("S", "s:0", "http://x")])
    before = eng.frontier_size
    eng.grow(cycles=2, start=0.0)
    assert eng.frontier_size > before   # frontier compounded


def test_run_cycle_max_endpoints_caps_work():
    eng = _engine()
    rep = eng.run_cycle(now=0.0, max_endpoints=3)
    assert rep.fetched_endpoints == 3


def test_grow_requires_positive_cycles():
    with pytest.raises(ValueError):
        _engine().grow(cycles=0)


def test_engine_survives_fetcher_errors():
    def boom(endpoint, now):
        raise RuntimeError("network down")
    eng = GrowthEngine(fetcher=boom)
    eng.add_endpoints([Endpoint("S", "s:0", "http://x")])
    rep = eng.run_cycle(now=0.0)         # must not raise
    assert rep.new_records == 0
    assert eng.stats["S"].errors == 1


def test_summary_shape():
    eng = _engine()
    eng.grow(cycles=2, start=0.0)
    s = eng.summary(now=10.0)
    assert s["cycles"] == 2 and "store" in s and "top_sources" in s


# ---- frontier from the real catalog ----

def test_iso_vocabulary_size():
    assert len(ISO_3166_ALPHA2) >= 240      # full ISO-3166-1 alpha-2 set
    assert "US" in ISO_3166_ALPHA2 and "UA" in ISO_3166_ALPHA2


def test_frontier_reaches_thousands():
    endpoints = build_frontier(catalog.sources())
    # Hundreds of parametric sources x 249 countries -> many thousands of endpoints.
    assert len(endpoints) >= 3000
    assert len({e.key for e in endpoints}) == len(endpoints)   # all unique


def test_frontier_feeds_engine():
    endpoints = build_frontier(catalog.sources())
    eng = GrowthEngine(fetcher=synthetic_fetcher(1))
    eng.add_endpoints(endpoints)
    assert eng.frontier_size == len(endpoints)
    rep = eng.run_cycle(now=0.0, max_endpoints=100)
    assert rep.new_records == 100          # 1 record per endpoint, 100 fetched


def test_parametric_sources_have_urls():
    for name, url in parametric_sources(catalog.sources()):
        assert url.startswith("http") and name
