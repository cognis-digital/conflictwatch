"""OFAC SDN cross-reference — flag conflict-event actors that appear on the
US Treasury Specially Designated Nationals (SDN) list.

A genuine enrichment for conflict OSINT: when an ingested ConflictEvent names an
actor (a militia, paramilitary group, vessel, individual, or state entity) that is
also an OFAC-sanctioned party, an analyst wants that surfaced immediately — it
changes the reporting, the legal posture, and the situational picture.

Data source (REAL, keyless, public):
    US Treasury OFAC SDN list  ->  https://www.treasury.gov/ofac/downloads/sdn.csv
    (the headerless ``sdn.csv`` flat file; columns documented at
    https://ofac.treasury.gov/specially-designated-nationals-and-blocked-persons-list-sdn-human-readable-lists)

The feed is fetched + cached by the bundled :mod:`conflictwatch.datafeeds` module
and re-served offline, so this works on disconnected / edge gear once cached.

Scope: descriptive OSINT / sanctions-screening for awareness only. Pure stdlib.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Iterable, Optional

from conflictwatch import datafeeds

SDN_FEED_ID = "ofac-sdn"

# Official OFAC sdn.csv column order (headerless flat file).
# https://ofac.treasury.gov/ ... /sdn-human-readable-lists
_SDN_COLUMNS = [
    "ent_num", "sdn_name", "sdn_type", "program", "title",
    "call_sign", "vess_type", "tonnage", "grt", "vess_flag",
    "vess_owner", "remarks",
]

# tokens too generic to be useful match keys on their own
_STOPWORDS = {
    "the", "of", "and", "for", "group", "front", "army", "forces", "force",
    "movement", "council", "party", "state", "national", "people", "popular",
    "republic", "islamic", "democratic", "liberation", "defense", "defence",
    "company", "ltd", "llc", "inc", "co", "al", "el", "and/or", "aka",
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).strip()


def _tokens(name: str) -> set[str]:
    return {t for t in _norm(name).split() if len(t) > 2 and t not in _STOPWORDS}


# pull a.k.a. / "..." aliases out of the OFAC remarks field
_ALIAS_RE = re.compile(r"a\.k\.a\.\s*'([^']+)'", re.IGNORECASE)


def _aliases(remarks: str) -> list[str]:
    return _ALIAS_RE.findall(remarks or "")


def parse_sdn_csv(text: str) -> list[dict]:
    """Parse the OFAC ``sdn.csv`` flat file into entity dicts.

    The file is headerless; OFAC uses ``-0-`` as an empty-field placeholder.
    """
    out: list[dict] = []
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        if not row or not row[0].strip():
            continue
        rec = {}
        for i, col in enumerate(_SDN_COLUMNS):
            val = row[i].strip() if i < len(row) else ""
            rec[col] = "" if val == "-0-" else val
        if not rec.get("sdn_name"):
            continue
        out.append(rec)
    return out


class SanctionsIndex:
    """In-memory token index over the OFAC SDN list for fast actor screening."""

    def __init__(self, entities: list[dict]):
        self.entities = entities
        self._by_token: dict[str, list[int]] = {}
        # index over the primary name AND any a.k.a. aliases in remarks
        self._names: list[set[str]] = []
        for idx, e in enumerate(entities):
            toks = _tokens(e.get("sdn_name", ""))
            for alias in _aliases(e.get("remarks", "")):
                toks |= _tokens(alias)
            self._names.append(toks)
            for tok in toks:
                self._by_token.setdefault(tok, []).append(idx)

    def __len__(self) -> int:
        return len(self.entities)

    def match(self, actor: str) -> list[dict]:
        """Return SDN entities whose name overlaps the actor name.

        A full token-subset (every significant actor token appears in the SDN
        name) is a strong hit; otherwise we require >=2 shared significant tokens
        to avoid spurious single-word collisions.
        """
        atoks = _tokens(actor)
        if not atoks:
            return []
        cand: dict[int, int] = {}
        for tok in atoks:
            for idx in self._by_token.get(tok, ()):
                cand[idx] = cand.get(idx, 0) + 1
        hits = []
        for idx, shared in cand.items():
            e = self.entities[idx]
            stoks = self._names[idx]
            subset = atoks.issubset(stoks) or stoks.issubset(atoks)
            if subset or shared >= 2:
                hits.append({
                    "sdn_name": e.get("sdn_name", ""),
                    "sdn_type": e.get("sdn_type", ""),
                    "program": e.get("program", ""),
                    "ent_num": e.get("ent_num", ""),
                    "shared_terms": shared,
                    "strong": bool(subset),
                })
        hits.sort(key=lambda h: (not h["strong"], -h["shared_terms"]))
        return hits


def load_index(*, offline: bool = False) -> SanctionsIndex:
    """Load the OFAC SDN list via the bundled datafeeds cache into an index.

    ``offline=True`` serves only from the local feed cache (edge / air-gap);
    otherwise the feed is refreshed if stale. Raises FileNotFoundError when
    offline with no cached snapshot.
    """
    text = datafeeds.get(SDN_FEED_ID, offline=offline)
    if isinstance(text, (bytes, bytearray)):
        text = text.decode("utf-8", "replace")
    return SanctionsIndex(parse_sdn_csv(text))


def screen_events(events: Iterable, *, offline: bool = False,
                  index: Optional[SanctionsIndex] = None) -> list[dict]:
    """Cross-reference each event's actors against the OFAC SDN list.

    Returns one record per event that has >=1 sanctioned actor:
        {event_id, date, country, actor, matches:[...]}
    Events are :class:`ConflictEvent` (or any object with .actor1/.actor2/.id).
    """
    idx = index or load_index(offline=offline)
    flagged: list[dict] = []
    for e in events:
        actors = [getattr(e, "actor1", "") or "", getattr(e, "actor2", "") or ""]
        ematches = []
        for actor in actors:
            for m in idx.match(actor):
                ematches.append({"actor": actor, **m})
        if ematches:
            flagged.append({
                "event_id": getattr(e, "id", ""),
                "date": getattr(e, "date", ""),
                "country": getattr(e, "country", ""),
                "actors": [a for a in actors if a],
                "matches": ematches,
            })
    return flagged
