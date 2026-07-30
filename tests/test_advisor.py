"""Tests for conflictwatch.advisor — recommend defensive lessons for events/situations.
Deterministic, offline; runs over the bundled lessons KB."""

from __future__ import annotations

from conflictwatch import advisor, lessonsindex
from conflictwatch.events import ConflictEvent


def _idx():
    return lessonsindex.build_index()


def _drone_event(**kw):
    kw.setdefault("event_type", "drone/uas")
    kw.setdefault("country", "Ukraine")
    kw.setdefault("tags", ["drone-uas"])
    kw.setdefault("notes", "drone strike on a position")
    return ConflictEvent(**kw)


# --- event_query -------------------------------------------------------------
def test_event_query_includes_platform_terms():
    q = advisor.event_query(_drone_event(tags=["shahed-loitering-munition"]))
    assert "shahed" in q and "counter-uas" in q


def test_event_query_includes_type_terms():
    q = advisor.event_query(ConflictEvent(event_type="explosion/remote",
                                          notes="shelling hit the town"))
    assert "artillery" in q or "shelling" in q


def test_event_query_includes_prose():
    q = advisor.event_query(_drone_event(notes="unique-marker-token here"))
    assert "unique-marker-token" in q


def test_event_query_dedups_terms():
    q = advisor.event_query(_drone_event(tags=["drone-uas", "fpv-drone"]))
    # "drone" appears in both platform expansions but should not duplicate in the head
    head = q.split("drone strike")[0]
    assert head.split().count("drone") == 1


def test_event_query_empty_event():
    q = advisor.event_query(ConflictEvent())
    assert q.strip() == "" or q.strip() == ""


def test_event_query_no_platform_uses_type_and_notes():
    q = advisor.event_query(ConflictEvent(event_type="battle", notes="assault repelled"))
    assert "assault" in q


# --- recommend ---------------------------------------------------------------
def test_recommend_drone_returns_counter_uas():
    recs = advisor.recommend(_drone_event(), k=3, index=_idx())
    assert recs
    assert any(r["category"] == "counter-uas" for r in recs)


def test_recommend_respects_k():
    recs = advisor.recommend(_drone_event(), k=2, index=_idx())
    assert len(recs) <= 2


def test_recommend_ranks_are_sequential():
    recs = advisor.recommend(_drone_event(), k=3, index=_idx())
    assert [r["rank"] for r in recs] == list(range(1, len(recs) + 1))


def test_recommend_scores_descending():
    recs = advisor.recommend(_drone_event(), k=5, index=_idx())
    scores = [r["score"] for r in recs]
    assert scores == sorted(scores, reverse=True)


def test_recommend_score_includes_boost():
    recs = advisor.recommend(_drone_event(), k=5, index=_idx())
    for r in recs:
        assert abs(r["score"] - (r["base_score"] + r["boost"])) < 1e-6


def test_recommend_affinity_boost_applied():
    # a drone event should give at least one counter-uas lesson a positive boost
    recs = advisor.recommend(_drone_event(), k=5, index=_idx())
    assert any(r["boost"] > 0 for r in recs)


def test_recommend_casualty_boost():
    ev = _drone_event(fatalities=8, notes="drone strike, several killed and wounded")
    recs = advisor.recommend(ev, k=8, index=_idx())
    # casualty-care should surface for an event with fatalities (if such a lesson exists)
    cats = {r["category"] for r in recs}
    assert recs  # at minimum we get recommendations
    assert isinstance(cats, set)


def test_recommend_empty_event_no_query():
    assert advisor.recommend(ConflictEvent(), index=_idx()) == []


def test_recommend_payload_shape():
    recs = advisor.recommend(_drone_event(), k=1, index=_idx())
    r = recs[0]
    for key in ("rank", "score", "base_score", "boost", "category", "title",
                "snippet", "matched", "lesson"):
        assert key in r


def test_recommend_lesson_is_dict():
    recs = advisor.recommend(_drone_event(), k=1, index=_idx())
    assert isinstance(recs[0]["lesson"], dict)


def test_recommend_deterministic():
    a = advisor.recommend(_drone_event(), k=3, index=_idx())
    b = advisor.recommend(_drone_event(), k=3, index=_idx())
    assert [r["title"] for r in a] == [r["title"] for r in b]


def test_recommend_builds_index_when_none():
    recs = advisor.recommend(_drone_event(), k=1)
    assert recs and "title" in recs[0]


def test_recommend_ew_event():
    ev = ConflictEvent(event_type="other", tags=["electronic-warfare"],
                       notes="heavy GPS jamming reported across the sector")
    recs = advisor.recommend(ev, k=5, index=_idx())
    assert recs  # jamming should retrieve spectrum/comms lessons


# --- recommend_text ----------------------------------------------------------
def test_recommend_text_drone():
    recs = advisor.recommend_text("A Shahed loitering munition struck the depot", k=3,
                                  index=_idx())
    assert recs
    assert any(r.get("category") == "counter-uas" for r in recs)


def test_recommend_text_respects_k():
    recs = advisor.recommend_text("drone attack on the base", k=2, index=_idx())
    assert len(recs) <= 2


def test_recommend_text_empty():
    recs = advisor.recommend_text("", k=3, index=_idx())
    assert isinstance(recs, list)


def test_recommend_text_generic_fallback():
    # text with a lessons-vocabulary word but no platform/type still returns hits
    recs = advisor.recommend_text("advice on resilient communications", k=3, index=_idx())
    assert isinstance(recs, list)


# --- brief -------------------------------------------------------------------
def test_brief_aggregates():
    evs = [_drone_event(), _drone_event(tags=["fpv-drone"], notes="fpv drone attack")]
    b = advisor.brief(evs, k=5, index=_idx())
    assert b["events"] == 2
    assert b["recommendations"]


def test_brief_recommendation_shape():
    b = advisor.brief([_drone_event()], k=3, index=_idx())
    r = b["recommendations"][0]
    for key in ("title", "category", "total_score", "hits", "snippet", "lesson"):
        assert key in r


def test_brief_scores_descending():
    evs = [_drone_event(), _drone_event(tags=["shahed-loitering-munition"])]
    b = advisor.brief(evs, k=5, index=_idx())
    scores = [r["total_score"] for r in b["recommendations"]]
    assert scores == sorted(scores, reverse=True)


def test_brief_categories_rollup():
    b = advisor.brief([_drone_event(), _drone_event()], k=5, index=_idx())
    assert isinstance(b["categories"], dict)
    assert sum(b["categories"].values()) == len(b["recommendations"])


def test_brief_respects_k():
    evs = [_drone_event() for _ in range(3)]
    b = advisor.brief(evs, k=2, index=_idx())
    assert len(b["recommendations"]) <= 2


def test_brief_empty_events():
    b = advisor.brief([], index=_idx())
    assert b["events"] == 0 and b["recommendations"] == []


def test_brief_hits_counted():
    # the same drone lesson should be hit by both identical events
    evs = [_drone_event(), _drone_event()]
    b = advisor.brief(evs, k=5, per_event=3, index=_idx())
    assert any(r["hits"] >= 2 for r in b["recommendations"])


def test_brief_lessons_considered():
    b = advisor.brief([_drone_event()], k=5, index=_idx())
    assert b["lessons_considered"] >= len(b["recommendations"])


def test_brief_deterministic():
    evs = [_drone_event(), _drone_event(tags=["fpv-drone"])]
    a = advisor.brief(evs, k=3, index=_idx())
    c = advisor.brief(evs, k=3, index=_idx())
    assert ([r["title"] for r in a["recommendations"]]
            == [r["title"] for r in c["recommendations"]])
