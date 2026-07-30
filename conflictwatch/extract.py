"""extract — pull structured entities and a normalized event out of free OSINT text.

Most OSINT arrives as prose: a headline, a wire snippet, a Telegram/RSS summary. Before
it can be analyzed, correlated, or deduped it has to become a `ConflictEvent` — *who did
what, where, when, how bad*. This module is a deterministic, stdlib-only "NER-lite" that
reads a sentence and lifts out:

  * **casualties**  — reported killed / wounded counts ("at least 6 killed, 12 wounded")
  * **dates**       — ISO, ``12 June 2026``, ``June 12, 2026``, ``06/12/2026`` forms
  * **actors**      — named forces/units ("Forces of A", "3rd Brigade", "X militia")
  * **places**      — country + place candidates via a small open gazetteer + "in/near X"
  * **platforms**   — the systems named (drone/FPV/Shahed, artillery, missile, ...)
  * **event type**  — mapped onto the ConflictEvent taxonomy via the shared heuristics

It is descriptive only — it structures *reported* text for awareness and analysis. It does
not resolve, geolocate to a strike coordinate, or target anything. Pure standard library,
deterministic, offline. Extraction is best-effort and always carries a confidence hint so a
human stays in the loop.
"""

from __future__ import annotations

import re
from collections import OrderedDict

from conflictwatch.events import ConflictEvent, _coerce_event_type, _iso_date

# --------------------------------------------------------------------------- #
# small open gazetteer — conflict-relevant countries (extend freely). Descriptive
# reference data, not targeting data; used only to tag which country prose mentions.
# --------------------------------------------------------------------------- #
COUNTRIES = (
    "Ukraine", "Russia", "Israel", "Palestine", "Gaza", "Lebanon", "Syria", "Iraq",
    "Iran", "Yemen", "Sudan", "South Sudan", "Somalia", "Ethiopia", "Mali", "Niger",
    "Burkina Faso", "Nigeria", "Libya", "Egypt", "Afghanistan", "Pakistan", "India",
    "Myanmar", "Armenia", "Azerbaijan", "Georgia", "Moldova", "Poland", "Turkey",
    "Saudi Arabia", "Colombia", "Mexico", "Democratic Republic of the Congo",
    "Central African Republic", "Mozambique", "Cameroon", "Chad", "Taiwan",
    "North Korea", "South Korea", "Philippines",
)

# platform / system keyword -> canonical tag (awareness taxonomy, not a weaponeering list)
_PLATFORMS = OrderedDict([
    (("fpv", "first-person-view"), "fpv-drone"),
    (("shahed", "geran"), "shahed-loitering-munition"),
    (("loitering munition", "kamikaze drone", "one-way attack", "owa drone"), "loitering-munition"),
    (("drone", "uav", "uas", "quadcopter"), "drone-uas"),
    (("himars", "grad", "artillery", "howitzer", "shelling"), "artillery"),
    (("ballistic missile", "cruise missile", "missile", "rocket"), "missile-rocket"),
    (("glide bomb", "kab", "guided bomb", "airstrike", "air strike", "air raid"), "air-delivered"),
    (("ied", "vbied", "roadside bomb", "landmine", "mine"), "ied-mine"),
    (("tank", "armor", "armoured", "apc", "ifv"), "armor"),
    (("jamming", "jammer", "spoofing", "electronic warfare", "ew "), "electronic-warfare"),
])

# unit / formation words that signal a named actor when preceded by a proper noun
_UNIT_WORDS = ("brigade", "battalion", "regiment", "division", "corps", "forces",
               "army", "militia", "group", "movement", "front", "guard", "guards",
               "coalition", "faction", "insurgents", "militants", "fighters", "troops",
               "defense forces", "armed forces", "national army")

_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
              "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
              "twelve": 12, "dozen": 12, "dozens": 24, "scores": 40}

_KILLED_WORDS = ("killed", "dead", "died", "fatalities", "casualties are dead",
                 "left dead", "lost their lives", "perished")
_WOUNDED_WORDS = ("wounded", "injured", "hurt", "maimed")


def _num(token: str) -> int | None:
    token = token.strip().lower().replace(",", "")
    if token.isdigit():
        return int(token)
    return _NUM_WORDS.get(token)


# --------------------------------------------------------------------------- #
# casualties
# --------------------------------------------------------------------------- #
def extract_casualties(text: str) -> dict:
    """Reported ``killed`` and ``wounded`` counts from prose (best-effort, max wins).

    Handles both orders ("6 killed" / "killed 6"), "at least"/"up to" qualifiers,
    and small number-words ("a dozen wounded"). Returns ``{"killed": int, "wounded": int}``
    with 0 when nothing is stated. Descriptive tallies of *reported* harm only.
    """
    t = (text or "").lower()
    killed, wounded = 0, 0
    num = r"(\d[\d,]*|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|dozens?|scores)"
    for word in _KILLED_WORDS:
        for m in re.finditer(rf"(?:at least |up to |as many as |some |about |around )?{num}\s+(?:people\s+|civilians\s+|soldiers\s+|were\s+|are\s+|reported\s+)*(?:{re.escape(word)})", t):
            killed = max(killed, _num(m.group(1)) or 0)
        for m in re.finditer(rf"{re.escape(word)}\s+(?:at least |up to |some |about )?{num}", t):
            killed = max(killed, _num(m.group(1)) or 0)
    for word in _WOUNDED_WORDS:
        for m in re.finditer(rf"(?:at least |up to |as many as |some |about |around )?{num}\s+(?:people\s+|others\s+|civilians\s+|were\s+|are\s+|reported\s+)*(?:{re.escape(word)})", t):
            wounded = max(wounded, _num(m.group(1)) or 0)
        for m in re.finditer(rf"{re.escape(word)}\s+(?:at least |up to |some |about )?{num}", t):
            wounded = max(wounded, _num(m.group(1)) or 0)
    return {"killed": killed, "wounded": wounded}


# --------------------------------------------------------------------------- #
# dates
# --------------------------------------------------------------------------- #
_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december")
_DATE_PATTERNS = (
    r"\b(\d{4}-\d{2}-\d{2})\b",
    r"\b(\d{1,2}\s+(?:%s)\s+\d{4})\b" % "|".join(_MONTHS),
    r"\b((?:%s)\s+\d{1,2},?\s+\d{4})\b" % "|".join(_MONTHS),
    r"\b(\d{1,2}/\d{1,2}/\d{4})\b",
)


def _norm_date(raw: str) -> str:
    """Normalize one date string (ISO / ``12 June 2026`` / ``June 12, 2026`` / slash)."""
    s = raw.strip().rstrip(",")
    from datetime import datetime
    for fmt in ("%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y",
                "%B %d, %Y", "%b %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return _iso_date(raw)


def extract_dates(text: str) -> list[str]:
    """Every parseable date in the text, normalized to ISO ``YYYY-MM-DD``, in order.

    Recognizes ISO, ``12 June 2026``, ``June 12, 2026`` and ``MM/DD/YYYY``. Duplicate
    ISO results are collapsed while preserving first-seen order.
    """
    out: list[str] = []
    low = (text or "")
    for pat in _DATE_PATTERNS:
        for m in re.finditer(pat, low, re.IGNORECASE):
            iso = _norm_date(m.group(1))
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso) and iso not in out:
                out.append(iso)
    return out


def first_date(text: str) -> str:
    ds = extract_dates(text)
    return ds[0] if ds else ""


# --------------------------------------------------------------------------- #
# platforms / systems
# --------------------------------------------------------------------------- #
def extract_platforms(text: str) -> list[str]:
    """Canonical tags for the systems the text names (drone/FPV/Shahed, artillery, ...).

    Returns a de-duplicated, deterministically-ordered list of platform tags. Awareness
    taxonomy only — identifies *what kind of system was reported*, nothing actionable.
    """
    t = (text or "").lower()
    out: list[str] = []
    for keys, tag in _PLATFORMS.items():
        if any(k in t for k in keys) and tag not in out:
            out.append(tag)
    return out


# --------------------------------------------------------------------------- #
# actors / named forces
# --------------------------------------------------------------------------- #
_ACTOR_RE = re.compile(
    r"\b((?:[A-Z][\w.\-]+\s+){0,3}(?:%s))\b" % "|".join(sorted(_UNIT_WORDS, key=len, reverse=True)),
    re.IGNORECASE)
_FORCES_OF_RE = re.compile(r"\bForces of ([A-Z][\w\-]*(?:\s+(?:of|the|and|[A-Z][\w\-]*))*)")


def extract_actors(text: str) -> list[str]:
    """Named forces / units mentioned ("Forces of A", "3rd Brigade", "X militia").

    Two heuristics: an explicit ``Forces of <Name>`` pattern, and a proper-noun run
    ending in a formation word (brigade/battalion/militia/...). De-duplicated, first-seen
    order preserved. Descriptive attribution of *reported* actors — no resolution to
    persons or coordinates.
    """
    text = text or ""
    out: list[str] = []
    for m in _FORCES_OF_RE.finditer(text):
        name = "Forces of " + m.group(1).strip()
        if name not in out:
            out.append(name)
    for m in _ACTOR_RE.finditer(text):
        phrase = re.sub(r"\s+", " ", m.group(1).strip())
        # keep only phrases that carry an uppercase proper-noun token before the unit word
        toks = phrase.split()
        if len(toks) >= 2 and any(w[:1].isupper() or w[:1].isdigit() for w in toks[:-1]):
            cleaned = phrase
            if cleaned not in out and not cleaned.lower().startswith("forces of"):
                out.append(cleaned)
    return out


# --------------------------------------------------------------------------- #
# places
# --------------------------------------------------------------------------- #
_PLACE_PREP_RE = re.compile(r"\b(?:in|near|at|outside|around)\s+([A-Z][\w'\-]+(?:\s+[A-Z][\w'\-]+){0,2})")


def extract_country(text: str, gazetteer: tuple = COUNTRIES) -> str:
    """The first gazetteer country named in the text ("" if none). Longest names first
    so "South Sudan" wins over "Sudan"."""
    t = text or ""
    for c in sorted(gazetteer, key=len, reverse=True):
        if re.search(r"\b" + re.escape(c) + r"\b", t, re.IGNORECASE):
            return c
    return ""


def extract_places(text: str) -> list[str]:
    """Candidate place names from ``in/near/at <Proper Noun>`` patterns (de-duplicated).

    A cheap gazetteer-free place-candidate extractor for the location field; pairs with
    :func:`extract_country`. Filters out obvious month/actor false positives.
    """
    text = text or ""
    out: list[str] = []
    stop = {m.capitalize() for m in _MONTHS} | {"The", "A", "An"}
    for m in _PLACE_PREP_RE.finditer(text):
        cand = m.group(1).strip()
        head = cand.split()[0]
        if head in stop:
            continue
        if any(cand.lower().endswith(u) for u in _UNIT_WORDS):
            continue
        if cand not in out:
            out.append(cand)
    return out


# --------------------------------------------------------------------------- #
# full extraction
# --------------------------------------------------------------------------- #
def classify_event_type(text: str) -> str:
    """Map free text onto the ConflictEvent taxonomy via the shared coercion heuristics."""
    return _coerce_event_type(text or "")


def _confidence(fields: dict) -> str:
    """A coarse extraction-confidence hint from how many strong fields were recovered."""
    score = 0
    score += 1 if fields.get("date") else 0
    score += 1 if fields.get("country") or fields.get("location") else 0
    score += 1 if fields.get("actor1") else 0
    score += 1 if fields.get("event_type") not in ("", "other") else 0
    score += 1 if (fields.get("fatalities") or fields.get("_wounded")) else 0
    return "high" if score >= 4 else "medium" if score >= 2 else "low"


def extract(text: str, *, source: str = "", source_url: str = "",
            default_date: str = "") -> dict:
    """Structured extraction from one piece of prose.

    Returns a dict with the recovered fields plus ``platforms``, ``wounded``,
    ``actors`` (all found) and a ``confidence`` hint::

        {date, event_type, actor1, actor2, country, location, fatalities,
         wounded, platforms, actors, source, source_url, notes, confidence}

    Everything is best-effort; missing fields come back empty/zero. Descriptive only.
    """
    text = (text or "").strip()
    cas = extract_casualties(text)
    actors = extract_actors(text)
    places = extract_places(text)
    country = extract_country(text)
    date = first_date(text) or default_date
    etype = classify_event_type(text)
    location = ""
    for p in places:
        if p != country:
            location = p
            break
    fields = {
        "date": date,
        "event_type": etype,
        "actor1": actors[0] if actors else "",
        "actor2": actors[1] if len(actors) > 1 else "",
        "country": country,
        "location": location,
        "fatalities": cas["killed"],
        "wounded": cas["wounded"],
        "platforms": extract_platforms(text),
        "actors": actors,
        "source": source,
        "source_url": source_url,
        "notes": text[:280],
    }
    fields["confidence"] = _confidence({**fields, "_wounded": cas["wounded"]})
    return fields


def to_event(text: str, *, source: str = "", source_url: str = "",
             default_date: str = "") -> ConflictEvent:
    """Build a :class:`ConflictEvent` from prose, tagging platforms + confidence.

    Platform tags and an ``extracted:<confidence>`` marker are attached to ``tags`` so the
    provenance of the record (machine-lifted from text) stays visible downstream.
    """
    f = extract(text, source=source, source_url=source_url, default_date=default_date)
    tags = list(f["platforms"]) + [f"extracted:{f['confidence']}"]
    if f["wounded"]:
        tags.append(f"wounded:{f['wounded']}")
    return ConflictEvent(
        date=f["date"], event_type=f["event_type"], actor1=f["actor1"], actor2=f["actor2"],
        country=f["country"], location=f["location"], fatalities=f["fatalities"],
        source=source, source_url=source_url, notes=f["notes"], tags=tags)


def extract_all(texts, *, source: str = "", default_date: str = "") -> list[ConflictEvent]:
    """Extract a :class:`ConflictEvent` from each text in an iterable (order preserved)."""
    return [to_event(t, source=source, default_date=default_date) for t in texts if (t or "").strip()]
