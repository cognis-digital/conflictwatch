"""Tests for conflictwatch.correlate — clusters, actor network, co-occurrence,
coordinated days. All deterministic and offline; events built in-memory or loaded
from the committed demos/sample_correlation.json fixture."""

from __future__ import annotations

import json
import os

from conflictwatch import correlate
from conflictwatch.events import ConflictEvent
from conflictwatch.sources import parse_generic_json
from conflictwatch.cli import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "demos", "sample_correlation.json")


def _fixture():
    with open(FIXTURE, encoding="utf-8") as fh:
        return parse_generic_json(fh.read())


def _ev(**kw):
    kw.setdefault("country", "X")
    kw.setdefault("event_type", "battle")
    return ConflictEvent(**kw)


# --- haversine ---------------------------------------------------------------
def test_haversine_zero():
    assert correlate.haversine_km(0, 0, 0, 0) == 0.0


def test_haversine_known_distance():
    # ~111 km per degree of latitude near the equator
    d = correlate.haversine_km(0, 0, 1, 0)
    assert 110 < d < 112


# --- clusters ----------------------------------------------------------------
def test_clusters_empty():
    assert correlate.clusters([]) == []


def test_clusters_finds_two_groups():
    cls = correlate.clusters(_fixture())
    assert len(cls) == 2
    # sorted worst-first: the 5-event Rivergate cluster leads
    assert cls[0]["size"] == 5 and cls[0]["fatalities"] == 16
    assert cls[1]["size"] == 3


def test_clusters_excludes_singletons():
    # the two scattered singletons must not appear in any cluster
    cls = correlate.clusters(_fixture())
    total = sum(c["size"] for c in cls)
    assert total == 8  # 10 events - 2 singletons


def test_clusters_respect_min_size():
    assert correlate.clusters(_fixture(), min_size=6) == []


def test_clusters_radius_splits():
    # a tiny radius must break the Rivergate group apart
    cls = correlate.clusters(_fixture(), radius_km=0.5)
    assert all(c["size"] < 5 for c in cls)


def test_clusters_skip_ungeolocated():
    evs = [_ev(date="2026-06-01"), _ev(date="2026-06-02"), _ev(date="2026-06-03")]
    assert correlate.clusters(evs) == []  # no lat/lon -> nothing to place


def test_clusters_deterministic():
    a = json.dumps(correlate.clusters(_fixture()), sort_keys=True)
    b = json.dumps(correlate.clusters(list(reversed(_fixture()))), sort_keys=True)
    assert a == b


# --- actor network -----------------------------------------------------------
def test_actor_network_edges_and_nodes():
    net = correlate.actor_network(_fixture())
    assert net["nodes"] and net["edges"]
    top = net["edges"][0]
    assert top["source"] == "Forces of A" and top["target"] == "Forces of B"
    assert top["weight"] == 2 and top["fatalities"] == 6


def test_actor_network_min_weight_filters():
    net = correlate.actor_network(_fixture(), min_weight=2)
    assert all(e["weight"] >= 2 for e in net["edges"])


def test_actor_network_ignores_blank():
    evs = [_ev(actor1="Solo", actor2="", date="2026-06-01")]
    net = correlate.actor_network(evs)
    assert net["edges"] == []
    assert net["nodes"][0]["actor"] == "Solo"


# --- co-occurrence -----------------------------------------------------------
def test_cooccurrence_pairs_recur():
    co = correlate.cooccurrence(_fixture(), window_days=4, min_count=1)
    assert co
    # every pair is two distinct event types
    assert all(p["types"][0] != p["types"][1] for p in co)


def test_cooccurrence_min_count():
    assert correlate.cooccurrence(_fixture(), min_count=99) == []


# --- coordinated days --------------------------------------------------------
def test_coordinated_days():
    # build a day with 3 distinct locations active
    evs = [_ev(date="2026-06-10", location="L1"),
           _ev(date="2026-06-10", location="L2"),
           _ev(date="2026-06-10", location="L3"),
           _ev(date="2026-06-11", location="L1")]
    cd = correlate.coordinated_days(evs, min_locations=3)
    assert len(cd) == 1 and cd[0]["date"] == "2026-06-10"
    assert cd[0]["locations"] == 3


def test_coordinated_days_threshold():
    evs = [_ev(date="2026-06-10", location="L1"),
           _ev(date="2026-06-10", location="L2")]
    assert correlate.coordinated_days(evs, min_locations=3) == []


# --- summary -----------------------------------------------------------------
def test_summary_structure():
    s = correlate.summary(_fixture())
    assert set(s) >= {"cluster_count", "clusters", "actor_network",
                      "cooccurrence", "coordinated_days", "largest_cluster"}
    assert s["cluster_count"] == len(s["clusters"])
    assert s["largest_cluster"]["size"] == 5


# --- CLI ---------------------------------------------------------------------
def _run(argv, capsys):
    rc = main(argv)
    return rc, capsys.readouterr().out


def test_cli_correlate_clusters(capsys):
    rc, out = _run(["correlate", FIXTURE, "--mode", "clusters"], capsys)
    assert rc == 0 and "cluster" in out


def test_cli_correlate_json(capsys):
    rc, out = _run(["correlate", FIXTURE, "--mode", "actor-network",
                    "--format", "json"], capsys)
    assert rc == 0
    data = json.loads(out)
    assert "nodes" in data and "edges" in data


def test_cli_correlate_all(capsys):
    rc, out = _run(["correlate", FIXTURE, "--mode", "all"], capsys)
    assert rc == 0 and "cluster" in out and "actor network" in out


def test_cli_correlate_rejects_bad_mode(capsys):
    import pytest
    with pytest.raises(SystemExit):
        _run(["correlate", FIXTURE, "--mode", "telepathy"], capsys)
