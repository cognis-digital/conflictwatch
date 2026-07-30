"""Tests for conflictwatch.lessonsindex — BM25 inverted index + ranked lessons search.
Deterministic, offline; uses the bundled lessons KB and small in-memory corpora."""

from __future__ import annotations

import json

from conflictwatch import lessonsindex
from conflictwatch.cli import main


DOCS = [
    {"category": "counter-uas", "title": "Drone detection nets",
     "insight": "Acoustic and RF sensors detect small drones early",
     "indicators": ["buzzing overhead"], "countermeasures": ["acoustic array"]},
    {"category": "ew-spectrum", "title": "GPS jamming resilience",
     "insight": "Navigation must survive GPS jamming and spoofing",
     "indicators": ["position drift"], "countermeasures": ["inertial backup"]},
    {"category": "casualty-care", "title": "Tourniquet basics",
     "insight": "Rapid tourniquet application saves lives in casualty care",
     "indicators": ["bleeding"], "countermeasures": ["tourniquet drills"]},
    {"category": "counter-uas", "title": "FPV drone threat",
     "insight": "FPV drones and acoustic detection matter for small drone defense",
     "indicators": ["fast movers"], "countermeasures": ["nets", "jammers"]},
]


def _idx(docs=DOCS, **kw):
    return lessonsindex.LessonIndex(docs, **kw)


# --- tokenize ----------------------------------------------------------------
def test_tokenize_basic():
    toks = lessonsindex.tokenize("Drone detection and jamming")
    assert toks == ["drone", "detection", "jamming"]


def test_tokenize_keeps_hyphen():
    assert "counter-uas" in lessonsindex.tokenize("counter-uas doctrine")


def test_tokenize_drops_stopwords():
    assert "the" not in lessonsindex.tokenize("the drone in the sky")


def test_tokenize_empty():
    assert lessonsindex.tokenize("") == []


# --- index construction ------------------------------------------------------
def test_index_builds():
    idx = _idx()
    assert idx.N == 4
    assert idx.avg_len > 0


def test_index_postings():
    idx = _idx()
    assert set(idx.postings["acoustic"]) == {0, 3}


def test_index_vocabulary_sorted():
    vocab = _idx().vocabulary()
    assert vocab == sorted(vocab)
    assert "drone" in vocab


def test_idf_nonneg():
    idx = _idx()
    assert all(idx.idf(t) >= 0 for t in idx.vocabulary())


def test_idf_unknown_term_zero():
    assert _idx().idf("zebra") == 0.0


def test_idf_rare_gt_common():
    idx = _idx()
    # 'tourniquet' appears in 1 doc, 'drone' in several -> rarer term higher idf
    assert idx.idf("tourniquet") > idx.idf("drone")


# --- search ------------------------------------------------------------------
def test_search_basic():
    hits = _idx().search("drone detection")
    assert hits and hits[0]["title"] in ("Drone detection nets", "FPV drone threat")


def test_search_ranks_by_relevance():
    hits = _idx().search("acoustic drone detection")
    # the two counter-uas drone docs should outrank casualty-care / ew
    top_cats = {h["category"] for h in hits[:2]}
    assert top_cats == {"counter-uas"}


def test_search_scores_descending():
    hits = _idx().search("drone acoustic detection")
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True)


def test_search_empty_query():
    assert _idx().search("") == []


def test_search_no_match():
    assert _idx().search("submarine periscope") == []


def test_search_k_limit():
    hits = _idx().search("drone acoustic jamming tourniquet detection", k=2)
    assert len(hits) <= 2


def test_search_category_filter():
    hits = _idx().search("drone acoustic", category="counter-uas")
    assert all(h["category"] == "counter-uas" for h in hits)


def test_search_rank_field():
    hits = _idx().search("drone acoustic")
    assert [h["rank"] for h in hits] == list(range(1, len(hits) + 1))


def test_search_matched_terms():
    hits = _idx().search("jamming spoofing")
    top = hits[0]
    assert "jamming" in top["matched"]


def test_search_snippet_present():
    hits = _idx().search("tourniquet")
    assert "tourniquet" in hits[0]["snippet"].lower()


def test_search_deterministic():
    a = json.dumps([{k: v for k, v in h.items() if k != "lesson"}
                    for h in _idx().search("drone acoustic detection")], sort_keys=True)
    b = json.dumps([{k: v for k, v in h.items() if k != "lesson"}
                    for h in _idx().search("drone acoustic detection")], sort_keys=True)
    assert a == b


def test_search_bm25_params():
    # different k1 should still return the same top doc but potentially different scores
    hits_default = _idx().search("drone acoustic")
    hits_k1 = _idx(k1=2.0).search("drone acoustic")
    assert hits_default[0]["index"] == hits_k1[0]["index"]


# --- related -----------------------------------------------------------------
def test_related_excludes_self():
    rel = _idx().related(0)
    assert all(r["index"] != 0 for r in rel)


def test_related_finds_similar_uas():
    rel = _idx().related(0, k=3)
    # doc 3 (FPV drone threat) shares 'acoustic'/'drone' -> should be related to doc 0
    assert any(r["index"] == 3 for r in rel)


def test_related_similarity_descending():
    rel = _idx().related(3, k=3)
    sims = [r["similarity"] for r in rel]
    assert sims == sorted(sims, reverse=True)


def test_related_k_limit():
    assert len(_idx().related(0, k=1)) <= 1


def test_related_out_of_range():
    import pytest
    with pytest.raises(IndexError):
        _idx().related(99)


# --- keywords ----------------------------------------------------------------
def test_keywords_returns_terms():
    kws = _idx().keywords(2)
    assert "tourniquet" in kws


def test_keywords_n_limit():
    assert len(_idx().keywords(0, n=3)) <= 3


# --- snippet -----------------------------------------------------------------
def test_snippet_highlights():
    s = lessonsindex.snippet("a drone was detected overhead", ["drone"])
    assert "**drone**" in s


def test_snippet_no_match_head():
    s = lessonsindex.snippet("nothing relevant here at all", ["submarine"])
    assert s.startswith("nothing")


def test_snippet_empty():
    assert lessonsindex.snippet("", ["x"]) == ""


def test_snippet_collapses_whitespace():
    s = lessonsindex.snippet("a   b\n\nc", ["b"])
    assert "  " not in s.replace("**", "")


# --- module-level over bundled KB --------------------------------------------
def test_build_index_bundled():
    idx = lessonsindex.build_index()
    assert idx.N >= 10


def test_search_bundled_drone():
    hits = lessonsindex.search("drone detection", k=3)
    assert hits
    assert hits[0]["score"] > 0


def test_search_bundled_category():
    hits = lessonsindex.search("drone", k=5, category="counter-uas")
    assert all(h["category"] == "counter-uas" for h in hits)


# --- CLI ---------------------------------------------------------------------
def _run(argv, capsys):
    rc = main(argv)
    return rc, capsys.readouterr().out


def test_cli_kbsearch(capsys):
    rc, out = _run(["kbsearch", "drone", "detection"], capsys)
    assert rc == 0 and "lesson(s)" in out


def test_cli_kbsearch_json(capsys):
    rc, out = _run(["kbsearch", "drone", "detection", "--format", "json"], capsys)
    assert rc == 0
    data = json.loads(out)
    assert isinstance(data, list)
    if data:
        assert "score" in data[0] and "lesson" not in data[0]


def test_cli_kbsearch_top(capsys):
    rc, out = _run(["kbsearch", "drone", "-k", "2", "--format", "json"], capsys)
    assert rc == 0
    assert len(json.loads(out)) <= 2
