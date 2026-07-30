"""Tests for conflictwatch.dedupstore — incremental streaming near-duplicate store.
Deterministic for a fixed arrival order, offline; events built in-memory."""

from __future__ import annotations

from conflictwatch import dedupstore
from conflictwatch.events import ConflictEvent


def _ev(**kw):
    kw.setdefault("country", "Ukraine")
    kw.setdefault("event_type", "drone/uas")
    return ConflictEvent(**kw)


def _pair():
    return (
        _ev(date="2026-06-12", location="Kramatorsk", fatalities=4, source="ACLED",
            notes="drone strike kills four in Kramatorsk"),
        _ev(date="2026-06-12", location="Kramatorsk", fatalities=6, source="GDELT",
            notes="drone strike kills people in Kramatorsk"),
    )


# --- add: new vs duplicate ---------------------------------------------------
def test_add_first_is_new():
    s = dedupstore.DedupStore()
    r = s.add(_ev(date="2026-06-12", location="K", notes="drone strike kills four"))
    assert r["is_new"] is True and r["cluster_id"] == 0 and r["size"] == 1


def test_add_duplicate_not_new():
    a, b = _pair()
    s = dedupstore.DedupStore()
    s.add(a)
    r = s.add(b)
    assert r["is_new"] is False and r["size"] == 2


def test_add_duplicate_same_cluster_id():
    a, b = _pair()
    s = dedupstore.DedupStore()
    r1 = s.add(a)
    r2 = s.add(b)
    assert r1["cluster_id"] == r2["cluster_id"]


def test_add_distinct_incident_new_cluster():
    s = dedupstore.DedupStore()
    s.add(_ev(date="2026-06-12", location="Kramatorsk", notes="drone strike kills four"))
    r = s.add(_ev(date="2026-07-01", location="Omdurman", country="Sudan",
                  event_type="battle", notes="clashes in Omdurman leave ten dead"))
    assert r["is_new"] is True and r["cluster_id"] == 1


def test_len_counts_incidents():
    a, b = _pair()
    s = dedupstore.DedupStore()
    s.add(a)
    s.add(b)
    assert len(s) == 1


def test_duplicates_count():
    a, b = _pair()
    s = dedupstore.DedupStore()
    s.add(a)
    s.add(b)
    assert s.duplicates() == 1


def test_size_of_cluster():
    a, b = _pair()
    s = dedupstore.DedupStore()
    cid = s.add(a)["cluster_id"]
    s.add(b)
    assert s.size(cid) == 2


def test_size_unknown_cluster_zero():
    s = dedupstore.DedupStore()
    assert s.size(99) == 0


# --- canonical folding -------------------------------------------------------
def test_canonical_takes_max_fatalities():
    a, b = _pair()
    s = dedupstore.DedupStore()
    s.add(a)
    r = s.add(b)
    assert r["canonical"].fatalities == 6


def test_canonical_merges_sources():
    a, b = _pair()
    s = dedupstore.DedupStore()
    s.add(a)
    r = s.add(b)
    assert "ACLED" in r["canonical"].source and "GDELT" in r["canonical"].source


def test_canonical_earliest_date():
    s = dedupstore.DedupStore()
    s.add(_ev(date="2026-06-12", location="K", notes="drone strike kills four"))
    r = s.add(_ev(date="2026-06-11", location="K", notes="drone strike kills four now"))
    assert r["canonical"].date == "2026-06-11"


def test_canonical_has_merged_tag():
    a, b = _pair()
    s = dedupstore.DedupStore()
    s.add(a)
    r = s.add(b)
    assert "merged:2" in r["canonical"].tags


def test_three_reports_one_incident():
    a, b = _pair()
    c = _ev(date="2026-06-12", location="Kramatorsk", fatalities=5, source="wire",
            notes="drone strike kills several in Kramatorsk")
    s = dedupstore.DedupStore()
    s.add(a)
    s.add(b)
    r = s.add(c)
    assert r["size"] == 3 and len(s) == 1


# --- canonical_events / cluster ----------------------------------------------
def test_canonical_events_deduped():
    a, b = _pair()
    s = dedupstore.DedupStore()
    s.add_many([a, b])
    evs = s.canonical_events()
    assert len(evs) == 1 and evs[0].fatalities == 6


def test_canonical_events_ordered_by_cluster():
    s = dedupstore.DedupStore()
    s.add(_ev(date="2026-06-12", location="Kramatorsk", notes="drone strike kills four"))
    s.add(_ev(date="2026-07-01", location="Omdurman", country="Sudan",
              event_type="battle", notes="clashes leave ten dead"))
    evs = s.canonical_events()
    assert len(evs) == 2


def test_cluster_returns_members():
    a, b = _pair()
    s = dedupstore.DedupStore()
    cid = s.add(a)["cluster_id"]
    s.add(b)
    members = s.cluster(cid)
    assert len(members) == 2
    assert {m.source for m in members} == {"ACLED", "GDELT"}


def test_cluster_unknown_empty():
    s = dedupstore.DedupStore()
    assert s.cluster(5) == []


# --- add_many ----------------------------------------------------------------
def test_add_many_results():
    a, b = _pair()
    s = dedupstore.DedupStore()
    results = s.add_many([a, b])
    assert results[0]["is_new"] is True and results[1]["is_new"] is False


def test_add_many_empty():
    s = dedupstore.DedupStore()
    assert s.add_many([]) == [] and len(s) == 0


# --- blocking correctness ----------------------------------------------------
def test_blocking_different_country_never_merges():
    s = dedupstore.DedupStore()
    s.add(_ev(date="2026-06-12", location="Town", country="Ukraine",
              notes="drone strike kills four"))
    r = s.add(_ev(date="2026-06-12", location="Town", country="Sudan",
                  notes="drone strike kills four"))
    assert r["is_new"] is True


def test_blocking_day_gap_within_window():
    s = dedupstore.DedupStore(max_day_gap=1)
    s.add(_ev(date="2026-06-01", location="K", notes="drone strike kills four"))
    r = s.add(_ev(date="2026-06-02", location="K", notes="drone strike kills four"))
    assert r["is_new"] is False


def test_blocking_day_gap_exceeded():
    s = dedupstore.DedupStore(max_day_gap=1)
    s.add(_ev(date="2026-06-01", location="K", notes="drone strike kills four"))
    r = s.add(_ev(date="2026-06-10", location="K", notes="drone strike kills four"))
    assert r["is_new"] is True


def test_undated_never_merges():
    s = dedupstore.DedupStore()
    s.add(_ev(date="", location="K", notes="drone strike kills four"))
    r = s.add(_ev(date="", location="K", notes="drone strike kills four"))
    assert r["is_new"] is True


def test_reindex_allows_later_match_after_date_shift():
    # canonical takes the earliest date; a later report a day before the canonical
    # should still land in the same incident thanks to reindexing
    s = dedupstore.DedupStore(max_day_gap=1)
    s.add(_ev(date="2026-06-12", location="K", notes="drone strike kills four"))
    s.add(_ev(date="2026-06-11", location="K", notes="drone strike kills four now"))
    r = s.add(_ev(date="2026-06-10", location="K", notes="drone strike kills four again"))
    assert r["is_new"] is False and r["size"] == 3


# --- report ------------------------------------------------------------------
def test_report_counts():
    a, b = _pair()
    s = dedupstore.DedupStore()
    s.add_many([a, b])
    rep = s.report()
    assert rep["input"] == 2 and rep["output"] == 1 and rep["removed"] == 1


def test_report_groups_merged():
    a, b = _pair()
    s = dedupstore.DedupStore()
    s.add_many([a, b])
    assert s.report()["groups_merged"] == 1


def test_report_largest_group():
    a, b = _pair()
    c = _ev(date="2026-06-12", location="Kramatorsk", fatalities=5, source="wire",
            notes="drone strike kills several in Kramatorsk")
    s = dedupstore.DedupStore()
    s.add_many([a, b, c])
    assert s.report()["largest_group"] == 3


def test_report_cluster_sources():
    a, b = _pair()
    s = dedupstore.DedupStore()
    s.add_many([a, b])
    cl = s.report()["clusters"][0]
    assert "ACLED" in cl["sources"] and "GDELT" in cl["sources"]


def test_report_no_dupes():
    s = dedupstore.DedupStore()
    s.add(_ev(date="2026-06-01", location="A", notes="alpha unique event"))
    s.add(_ev(date="2026-07-01", location="B", country="Sudan",
              notes="beta different event"))
    rep = s.report()
    assert rep["removed"] == 0 and rep["groups_merged"] == 0
    assert rep["largest_group"] == 1


def test_report_empty_store():
    rep = dedupstore.DedupStore().report()
    assert rep["input"] == 0 and rep["output"] == 0 and rep["clusters"] == []


# --- dedup_stream convenience ------------------------------------------------
def test_dedup_stream_returns_canonical_and_report():
    a, b = _pair()
    canon, rep = dedupstore.dedup_stream([a, b])
    assert len(canon) == 1 and rep["removed"] == 1


def test_dedup_stream_empty():
    canon, rep = dedupstore.dedup_stream([])
    assert canon == [] and rep["input"] == 0


def test_dedup_stream_passthrough_uniques():
    evs = [_ev(date="2026-06-01", location="A", notes="alpha unique event"),
           _ev(date="2026-07-01", location="B", country="Sudan",
               notes="beta different event")]
    canon, rep = dedupstore.dedup_stream(evs)
    assert len(canon) == 2 and rep["removed"] == 0


def test_dedup_stream_matches_merge_output_size():
    from conflictwatch import merge
    evs = [
        _ev(date="2026-06-12", location="Kramatorsk", fatalities=4, source="ACLED",
            notes="drone strike kills four in Kramatorsk"),
        _ev(date="2026-06-12", location="Kramatorsk", fatalities=6, source="GDELT",
            notes="drone strike kills people in Kramatorsk"),
        _ev(date="2026-06-20", location="Omdurman", country="Sudan", event_type="battle",
            fatalities=10, source="ACLED", notes="clashes in Omdurman leave many dead"),
    ]
    _, rep_stream = dedupstore.dedup_stream(evs)
    _, rep_batch = merge.merge(evs)
    assert rep_stream["output"] == rep_batch["output"]


# --- custom thresholds -------------------------------------------------------
def test_custom_radius_km_far_apart():
    s = dedupstore.DedupStore(radius_km=5.0)
    s.add(_ev(date="2026-06-01", lat=48.0, lon=37.5, notes="drone strike alpha"))
    r = s.add(_ev(date="2026-06-01", lat=50.0, lon=40.0, notes="drone strike beta"))
    assert r["is_new"] is True


def test_custom_sim_threshold_strict():
    s = dedupstore.DedupStore(sim_threshold=0.99)
    s.add(_ev(date="2026-06-01", location="Town", event_type="other",
              notes="one two three four"))
    r = s.add(_ev(date="2026-06-01", location="Town", event_type="other",
                  notes="five six seven eight"))
    assert r["is_new"] is True
