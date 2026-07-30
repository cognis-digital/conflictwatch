"""advisor — recommend relevant 'what's working' lessons for an event or situation.

`lessonsindex` gives ranked retrieval over the lessons KB, but the analyst still has to hand
it a query. `advisor` closes the loop: given a :class:`ConflictEvent` (or a free-text
situation), it builds a query from what the event actually *is* — its platforms, event type,
and prose — biases toward the defensive lesson **categories** that match the threat, and
returns the top defensive, force-protection lessons via BM25.

  * **event -> query** — platform tags and event type expand into the vocabulary the lessons
    are written in (a Shahed report pulls "loitering munition / counter-UAS / detection")
  * **category affinity** — a small, transparent threat->category map (drone -> counter-uas,
    jamming -> ew-spectrum, casualties -> casualty-care, ...) gently boosts on-topic lessons
  * **situation briefs** — the same recommender runs over a batch of events, aggregating the
    lessons most relevant across the whole picture

Descriptive, defensive awareness tooling: it surfaces *published, sourced* countermeasure and
indicator lessons for force protection. It recommends nothing operational and targets nothing.
Pure standard library, deterministic, offline.
"""

from __future__ import annotations

from collections import defaultdict

from conflictwatch.events import ConflictEvent
from conflictwatch.lessonsindex import LessonIndex, build_index

# platform tag (from extract) -> query terms that appear in the lessons vocabulary
_PLATFORM_TERMS = {
    "fpv-drone": ("fpv", "drone", "counter-uas", "detection", "jamming"),
    "shahed-loitering-munition": ("shahed", "loitering", "munition", "drone", "counter-uas",
                                  "interceptor", "detection"),
    "loitering-munition": ("loitering", "munition", "drone", "counter-uas", "detection"),
    "drone-uas": ("drone", "uas", "counter-uas", "detection", "jamming"),
    "artillery": ("artillery", "shelling", "cover", "dispersal", "survivability"),
    "missile-rocket": ("missile", "rocket", "warning", "shelter", "air", "defense"),
    "air-delivered": ("airstrike", "air", "shelter", "cover", "survivability"),
    "ied-mine": ("ied", "mine", "route", "clearance", "mobility"),
    "armor": ("armor", "anti-tank", "obstacle", "mobility"),
    "electronic-warfare": ("jamming", "spoofing", "electronic", "warfare", "spectrum",
                           "comms", "pnt", "gps"),
}

# event type -> query terms
_TYPE_TERMS = {
    "drone/uas": ("drone", "uas", "counter-uas", "detection"),
    "explosion/remote": ("shelling", "artillery", "missile", "cover", "survivability"),
    "battle": ("assault", "defense", "survivability", "cover"),
    "violence against civilians": ("protection", "civilian", "casualty", "evacuation"),
    "riots": ("crowd", "public", "order"),
    "protests": ("crowd", "public", "order"),
    "strategic development": ("logistics", "sustainment", "movement"),
}

# threat signal -> lesson category that most directly addresses it (affinity boost)
_CATEGORY_AFFINITY = {
    "fpv-drone": "counter-uas",
    "shahed-loitering-munition": "counter-uas",
    "loitering-munition": "counter-uas",
    "drone-uas": "counter-uas",
    "electronic-warfare": "ew-spectrum",
    "ied-mine": "mobility",
    "armor": "mobility",
}

# how much a category-affinity match adds on top of the BM25 score (small, transparent)
AFFINITY_BOOST = 0.5
# an event with reported casualties nudges casualty-care lessons up
CASUALTY_CATEGORY = "casualty-care"
CASUALTY_BOOST = 0.4


def _platforms_of(event: ConflictEvent) -> list[str]:
    return [t for t in (event.tags or []) if t in _PLATFORM_TERMS]


def event_query(event: ConflictEvent) -> str:
    """Build a lessons-vocabulary query string from an event's platforms, type, and notes.

    Deterministic: platform terms first (in tag order), then event-type terms, then the
    event's own prose. Duplicate terms are collapsed while preserving first-seen order.
    """
    terms: list[str] = []
    for p in _platforms_of(event):
        terms.extend(_PLATFORM_TERMS.get(p, ()))
    terms.extend(_TYPE_TERMS.get(event.event_type, ()))
    seen, ordered = set(), []
    for t in terms:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    prose = (event.notes or "")
    return " ".join(ordered) + (" " + prose if prose else "")


def _affinity_categories(event: ConflictEvent) -> set:
    cats = {_CATEGORY_AFFINITY[p] for p in _platforms_of(event) if p in _CATEGORY_AFFINITY}
    if event.fatalities and event.fatalities > 0:
        cats.add(CASUALTY_CATEGORY)
    return cats


def recommend(event: ConflictEvent, *, k: int = 3,
              index: LessonIndex | None = None) -> list[dict]:
    """Top ``k`` defensive lessons relevant to a single event.

    Runs a BM25 search built from :func:`event_query`, then applies a small, transparent
    category-affinity boost (drone->counter-uas, jamming->ew-spectrum, casualties->
    casualty-care). Returns ``{rank, score, base_score, boost, category, title, snippet,
    matched, lesson}`` rows, best first. An event with no usable query yields ``[]``.
    """
    idx = index if index is not None else build_index()
    query = event_query(event)
    if not query.strip():
        return []
    # pull a generous candidate pool, then re-rank with affinity
    hits = idx.search(query, k=max(k * 4, k + 5))
    if not hits:
        return []
    affinity = _affinity_categories(event)
    ranked = []
    for h in hits:
        boost = 0.0
        cat = h.get("category", "")
        if cat in affinity:
            boost += AFFINITY_BOOST if cat != CASUALTY_CATEGORY else CASUALTY_BOOST
        ranked.append((h["score"] + boost, h["score"], boost, h))
    ranked.sort(key=lambda r: (-r[0], -r[1], r[3]["index"]))
    out = []
    for rank, (total, base, boost, h) in enumerate(ranked[:k], 1):
        out.append({
            "rank": rank,
            "score": round(total, 4),
            "base_score": round(base, 4),
            "boost": round(boost, 4),
            "category": h.get("category", ""),
            "title": h.get("title", ""),
            "snippet": h.get("snippet", ""),
            "matched": h.get("matched", []),
            "lesson": h.get("lesson", {}),
        })
    return out


def recommend_text(text: str, *, k: int = 3, source: str = "situation",
                   index: LessonIndex | None = None) -> list[dict]:
    """Recommend lessons for a free-text situation by extracting an event first.

    Delegates to :func:`conflictwatch.extract.to_event` so a raw report string benefits
    from platform/type detection before recommendation. Falls back to a plain BM25 search
    on the text when extraction yields nothing usable.
    """
    from conflictwatch.extract import to_event
    ev = to_event(text or "", source=source)
    rec = recommend(ev, k=k, index=index)
    if rec:
        return rec
    idx = index if index is not None else build_index()
    hits = idx.search(text or "", k=k)
    for h in hits:
        h.setdefault("base_score", h.get("score"))
        h.setdefault("boost", 0.0)
    return hits


def brief(events, *, k: int = 5, per_event: int = 3,
          index: LessonIndex | None = None) -> dict:
    """Aggregate the lessons most relevant across a batch of events.

    Recommends ``per_event`` lessons for each event, sums their boosted scores per lesson,
    and returns the ``k`` highest-aggregate lessons plus coverage stats::

        {events, lessons_considered, recommendations:[{title, category, total_score,
         hits, snippet, lesson}], categories:{cat: count}}

    Deterministic (ties break by title). Descriptive, defensive rollup only.
    """
    idx = index if index is not None else build_index()
    events = list(events)
    agg_score: dict[str, float] = defaultdict(float)
    agg_hits: dict[str, int] = defaultdict(int)
    payload: dict[str, dict] = {}
    cat_count: dict[str, int] = defaultdict(int)
    for e in events:
        for rec in recommend(e, k=per_event, index=idx):
            title = rec["title"]
            agg_score[title] += rec["score"]
            agg_hits[title] += 1
            payload[title] = rec
    ranked = sorted(agg_score.items(), key=lambda kv: (-kv[1], kv[0]))
    recommendations = []
    for title, total in ranked[:k]:
        rec = payload[title]
        cat_count[rec["category"]] += 1
        recommendations.append({
            "title": title,
            "category": rec["category"],
            "total_score": round(total, 4),
            "hits": agg_hits[title],
            "snippet": rec["snippet"],
            "lesson": rec["lesson"],
        })
    return {
        "events": len(events),
        "lessons_considered": len(agg_score),
        "recommendations": recommendations,
        "categories": dict(sorted(cat_count.items(), key=lambda kv: (-kv[1], kv[0]))),
    }
