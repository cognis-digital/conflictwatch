"""entities — actor/entity resolution and a registry across normalized events.

`extract` lifts actor *mentions* out of prose and `correlate.actor_network` counts who
appears with whom. But the same force is written a dozen ways across sources — "Russian
Armed Forces", "RU forces", "Forces of Russia", "russian troops" — so mention counts and
networks fracture. This module canonicalizes those surface forms and folds every event's
actors into one **entity registry**:

  * **canonicalization** — an alias gazetteer + deterministic surface-form cleanup maps a
    raw mention to a single canonical name (``Russian Armed Forces`` etc.)
  * **fuzzy resolution** — an unknown mention is attached to the closest known canonical by
    token-set (Jaccard) overlap above a threshold, so near-spellings collapse
  * **registry** — per entity: mention count, first/last seen date, the event types and
    platforms it is reported with, the countries it appears in, and its co-actors
  * **co-occurrence** — an undirected weighted graph of entities reported in the same event

Descriptive consolidation of *reported* actor names for cleaner awareness and analysis —
it normalizes labels, it does not resolve identities to persons, units-in-the-field, or
coordinates. Pure standard library, deterministic, offline.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from conflictwatch.events import ConflictEvent

# --------------------------------------------------------------------------- #
# alias gazetteer — common surface forms -> a single canonical label. Purely a
# labelling aid (descriptive), extend freely. Keys are matched case-insensitively
# after surface-form cleanup; longer/more-specific keys are tried first.
# --------------------------------------------------------------------------- #
ACTOR_ALIASES = {
    "russian armed forces": "Russian Armed Forces",
    "armed forces of russia": "Russian Armed Forces",
    "forces of russia": "Russian Armed Forces",
    "russian forces": "Russian Armed Forces",
    "russian troops": "Russian Armed Forces",
    "russian military": "Russian Armed Forces",
    "russian federation forces": "Russian Armed Forces",
    "ru forces": "Russian Armed Forces",
    "vs rf": "Russian Armed Forces",
    "armed forces of ukraine": "Armed Forces of Ukraine",
    "ukrainian armed forces": "Armed Forces of Ukraine",
    "forces of ukraine": "Armed Forces of Ukraine",
    "ukrainian forces": "Armed Forces of Ukraine",
    "ukrainian troops": "Armed Forces of Ukraine",
    "ukrainian military": "Armed Forces of Ukraine",
    "afu": "Armed Forces of Ukraine",
    "zsu": "Armed Forces of Ukraine",
    "israel defense forces": "Israel Defense Forces",
    "israeli defense forces": "Israel Defense Forces",
    "israeli forces": "Israel Defense Forces",
    "israeli military": "Israel Defense Forces",
    "idf": "Israel Defense Forces",
    "rapid support forces": "Rapid Support Forces",
    "rsf": "Rapid Support Forces",
    "sudanese armed forces": "Sudanese Armed Forces",
    "saf": "Sudanese Armed Forces",
    "islamic state": "Islamic State",
    "isis": "Islamic State",
    "isil": "Islamic State",
    "daesh": "Islamic State",
    "is-wp": "Islamic State",
    "wagner group": "Wagner Group",
    "wagner": "Wagner Group",
    "pmc wagner": "Wagner Group",
    "al-shabaab": "Al-Shabaab",
    "al shabaab": "Al-Shabaab",
    "boko haram": "Boko Haram",
    "hezbollah": "Hezbollah",
    "hizbollah": "Hezbollah",
    "hamas": "Hamas",
    "houthis": "Houthi Movement",
    "houthi": "Houthi Movement",
    "ansar allah": "Houthi Movement",
}

# words dropped from the *edges* of a surface form when cleaning (never from the middle)
_EDGE_NOISE = frozenset(("the", "a", "an", "of", "unknown", "unidentified", "suspected",
                         "alleged", "reported"))
_TOKEN_RE = re.compile(r"[a-z0-9]+")
# generic tail words that make two names "the same kind of thing" but aren't discriminating
_GENERIC = frozenset(("forces", "force", "army", "armed", "military", "troops", "group",
                      "movement", "militia", "brigade", "battalion", "regiment", "the",
                      "of", "a", "an"))


def clean_surface(name: str) -> str:
    """Whitespace-collapse a raw actor mention and trim leading/trailing noise words.

    Punctuation-light normalization only: internal words are preserved so distinct
    forces stay distinct. Returns "" for empty/all-noise input.
    """
    s = re.sub(r"\s+", " ", (name or "").strip())
    s = s.strip(" .,:;-–—")
    if not s:
        return ""
    toks = s.split()
    while toks and toks[0].lower() in _EDGE_NOISE:
        toks.pop(0)
    while toks and toks[-1].lower() in _EDGE_NOISE:
        toks.pop()
    return " ".join(toks)


def _key(name: str) -> str:
    return clean_surface(name).lower()


def _tokens(name: str) -> set:
    return set(_TOKEN_RE.findall((name or "").lower()))


def _content_tokens(name: str) -> set:
    """Discriminating tokens (generic force-words removed) for fuzzy matching."""
    return {t for t in _tokens(name) if t not in _GENERIC and len(t) > 1}


def canonical_actor(name: str, *, aliases: dict | None = None) -> str:
    """Map a raw actor mention onto its canonical label via the alias gazetteer.

    Falls back to a cleaned, title-cased surface form when no alias matches. An acronym
    already in canonical case is preserved. Deterministic and idempotent
    (``canonical_actor(canonical_actor(x)) == canonical_actor(x)``).
    """
    aliases = ACTOR_ALIASES if aliases is None else aliases
    key = _key(name)
    if not key:
        return ""
    if key in aliases:
        return aliases[key]
    cleaned = clean_surface(name)
    # already a known canonical value? keep as-is
    if cleaned in set(aliases.values()):
        return cleaned
    return _titlecase(cleaned)


def _titlecase(s: str) -> str:
    small = {"of", "the", "and", "for", "al", "de", "la"}
    out = []
    for i, w in enumerate(s.split()):
        if w.isupper() and len(w) <= 5:      # keep acronyms (IDF, RSF, SAF)
            out.append(w)
        elif i and w.lower() in small:
            out.append(w.lower())
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


def resolve(name: str, known, *, threshold: float = 0.6,
            aliases: dict | None = None) -> str:
    """Resolve a mention to the closest *already-known* canonical name, else canonicalize.

    First tries the alias gazetteer/cleanup (:func:`canonical_actor`). If that canonical
    name is not among ``known``, the mention's discriminating token set is compared by
    Jaccard against each known entity; the best match at or above ``threshold`` wins.
    Ties break alphabetically for determinism. ``known`` is any iterable of canonical names.
    """
    canon = canonical_actor(name, aliases=aliases)
    if not canon:
        return ""
    known = list(known)
    if canon in known:
        return canon
    q = _content_tokens(canon)
    if not q:
        return canon
    best, best_score = canon, threshold
    for k in sorted(set(known)):
        kt = _content_tokens(k)
        if not kt:
            continue
        inter = len(q & kt)
        if not inter:
            continue
        score = inter / len(q | kt)
        if score >= best_score and (score > best_score or k < best):
            best, best_score = k, score
    return best


class EntityRegistry:
    """A registry of canonical actor entities folded from a list of events.

    Build with :meth:`from_events` (or feed events one at a time via :meth:`add`). Each
    entity accumulates: ``mentions``, ``first_seen``/``last_seen`` dates, a Counter of
    ``event_types`` and ``platforms`` it is reported with, the ``countries`` it appears in,
    and a co-actor Counter. Resolution reuses names already in the registry so spelling
    drift collapses onto the first canonical form seen. Deterministic.
    """

    def __init__(self, *, fuzzy: bool = True, threshold: float = 0.6,
                 aliases: dict | None = None):
        self.fuzzy = fuzzy
        self.threshold = threshold
        self.aliases = ACTOR_ALIASES if aliases is None else aliases
        self.entities: dict[str, dict] = {}
        self._surface: dict[str, set] = defaultdict(set)

    # ------------------------------------------------------------------ #
    def _resolve(self, raw: str) -> str:
        if self.fuzzy and self.entities:
            return resolve(raw, self.entities.keys(), threshold=self.threshold,
                           aliases=self.aliases)
        return canonical_actor(raw, aliases=self.aliases)

    def add(self, event: ConflictEvent) -> list[str]:
        """Fold one event's actors into the registry; return the canonical names touched."""
        raws = [a for a in (event.actor1, event.actor2) if a and a.strip()]
        # platform tags travel on the event's tags (see extract.to_event)
        platforms = [t for t in (event.tags or []) if _is_platform_tag(t)]
        names = []
        for raw in raws:
            canon = self._resolve(raw)
            if not canon:
                continue
            names.append(canon)
            ent = self.entities.get(canon)
            if ent is None:
                ent = self.entities[canon] = {
                    "name": canon, "mentions": 0,
                    "first_seen": "", "last_seen": "",
                    "event_types": Counter(), "platforms": Counter(),
                    "countries": Counter(), "co_actors": Counter(),
                }
            ent["mentions"] += 1
            self._surface[canon].add(clean_surface(raw))
            if event.date:
                if not ent["first_seen"] or event.date < ent["first_seen"]:
                    ent["first_seen"] = event.date
                if not ent["last_seen"] or event.date > ent["last_seen"]:
                    ent["last_seen"] = event.date
            if event.event_type:
                ent["event_types"][event.event_type] += 1
            if event.country:
                ent["countries"][event.country] += 1
            for p in platforms:
                ent["platforms"][p] += 1
        # co-actors: every unordered pair in this event
        uniq = list(dict.fromkeys(names))
        for i, a in enumerate(uniq):
            for b in uniq[i + 1:]:
                self.entities[a]["co_actors"][b] += 1
                self.entities[b]["co_actors"][a] += 1
        return names

    @classmethod
    def from_events(cls, events, **kwargs) -> "EntityRegistry":
        """Build a registry from an iterable of events (order affects only canonical-form
        choice on ties, never counts)."""
        reg = cls(**kwargs)
        for e in events:
            reg.add(e)
        return reg

    # ------------------------------------------------------------------ #
    def names(self) -> list[str]:
        """All canonical entity names, sorted."""
        return sorted(self.entities)

    def __len__(self) -> int:
        return len(self.entities)

    def __contains__(self, name: str) -> bool:
        return canonical_actor(name, aliases=self.aliases) in self.entities

    def surface_forms(self, name: str) -> list[str]:
        """The distinct raw surface forms that collapsed onto ``name`` (sorted)."""
        return sorted(self._surface.get(canonical_actor(name, aliases=self.aliases), ()))

    def profile(self, name: str) -> dict | None:
        """A JSON-friendly summary for one entity, or ``None`` if unknown.

        ``{name, mentions, first_seen, last_seen, event_types, platforms, countries,
        co_actors, surface_forms}`` with the Counters rendered as ordered dicts.
        """
        canon = self._resolve(name) if self.fuzzy else canonical_actor(name, aliases=self.aliases)
        ent = self.entities.get(canon) or self.entities.get(
            canonical_actor(name, aliases=self.aliases))
        if not ent:
            return None
        return {
            "name": ent["name"],
            "mentions": ent["mentions"],
            "first_seen": ent["first_seen"],
            "last_seen": ent["last_seen"],
            "event_types": dict(ent["event_types"].most_common()),
            "platforms": dict(ent["platforms"].most_common()),
            "countries": dict(ent["countries"].most_common()),
            "co_actors": dict(ent["co_actors"].most_common()),
            "surface_forms": sorted(self._surface.get(ent["name"], ())),
        }

    def top(self, n: int = 10) -> list[dict]:
        """The ``n`` most-mentioned entities as ``{name, mentions, countries}`` rows.

        Sorted by descending mentions then name (deterministic)."""
        rows = [(e["mentions"], name) for name, e in self.entities.items()]
        rows.sort(key=lambda r: (-r[0], r[1]))
        out = []
        for mentions, name in rows[:n]:
            ent = self.entities[name]
            out.append({"name": name, "mentions": mentions,
                        "countries": sorted(ent["countries"])})
        return out

    def cooccurrence(self, *, min_weight: int = 1) -> list[dict]:
        """Undirected weighted co-actor edges ``{a, b, weight}`` (a < b), weight-desc.

        Each entity pair reported together is one edge; ``weight`` counts shared events.
        Filtered to ``weight >= min_weight``. Deterministic ordering."""
        seen: dict[tuple, int] = {}
        for name, ent in self.entities.items():
            for other, w in ent["co_actors"].items():
                a, b = sorted((name, other))
                seen[(a, b)] = w        # symmetric — same value from either side
        edges = [{"a": a, "b": b, "weight": w} for (a, b), w in seen.items()
                 if w >= min_weight]
        edges.sort(key=lambda e: (-e["weight"], e["a"], e["b"]))
        return edges

    def summary(self) -> dict:
        """Registry-level rollup: entity/mention totals and the busiest entities."""
        total_mentions = sum(e["mentions"] for e in self.entities.values())
        return {
            "entities": len(self.entities),
            "mentions": total_mentions,
            "distinct_surface_forms": sum(len(v) for v in self._surface.values()),
            "top": self.top(5),
        }


def _is_platform_tag(tag: str) -> bool:
    """Heuristic: an extract-attached platform tag (not the extracted:/wounded:/merged: meta)."""
    t = (tag or "")
    return bool(t) and ":" not in t


# --------------------------------------------------------------------------- #
# module-level convenience
# --------------------------------------------------------------------------- #
def build_registry(events, **kwargs) -> EntityRegistry:
    """Build an :class:`EntityRegistry` from events (convenience wrapper)."""
    return EntityRegistry.from_events(events, **kwargs)


def canonicalize_events(events, *, aliases: dict | None = None) -> list[ConflictEvent]:
    """Return copies of ``events`` with ``actor1``/``actor2`` rewritten to canonical labels.

    Additive and non-destructive: input events are unchanged; new :class:`ConflictEvent`
    objects are returned with normalized actor names (all other fields preserved).
    """
    out: list[ConflictEvent] = []
    for e in events:
        out.append(ConflictEvent(
            date=e.date, event_type=e.event_type,
            actor1=canonical_actor(e.actor1, aliases=aliases),
            actor2=canonical_actor(e.actor2, aliases=aliases),
            country=e.country, region=e.region, location=e.location,
            lat=e.lat, lon=e.lon, fatalities=e.fatalities,
            source=e.source, source_url=e.source_url, notes=e.notes,
            tags=list(e.tags)))
    return out
