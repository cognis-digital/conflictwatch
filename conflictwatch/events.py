"""The conflict-event contract — one normalized shape for open-source conflict data.

Open conflict datasets (ACLED, GDELT, UCDP) and OSINT news all describe the same thing:
*who did what, where, when, and how bad*. `ConflictEvent` is the normalized record every
source maps to, so collection, dedup, analysis, and reporting work uniformly.

Scope: open-source situational awareness and analysis. Descriptive only — this models
*reported* events for monitoring and force protection, not targeting. Pure stdlib.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

# event taxonomy (ACLED-aligned, kept small and source-agnostic)
EVENT_TYPES = ("battle", "explosion/remote", "violence against civilians", "riots",
               "protests", "strategic development", "drone/uas", "other")

# severity tiers from reported fatalities + event type
SEVERITIES = ("info", "low", "medium", "high", "critical")


@dataclass
class ConflictEvent:
    date: str = ""                       # ISO-8601 (YYYY-MM-DD)
    event_type: str = "other"
    actor1: str = ""
    actor2: str = ""
    country: str = ""
    region: str = ""                     # admin1 / province
    location: str = ""                   # place name
    lat: float | None = None
    lon: float | None = None
    fatalities: int = 0
    source: str = ""                     # producing dataset / outlet
    source_url: str = ""
    notes: str = ""
    tags: list = field(default_factory=list)
    id: str = ""

    def __post_init__(self):
        if self.event_type not in EVENT_TYPES:
            self.event_type = _coerce_event_type(self.event_type)
        try:
            self.fatalities = int(self.fatalities or 0)
        except (TypeError, ValueError):
            self.fatalities = 0
        for f in ("lat", "lon"):
            v = getattr(self, f)
            if v not in (None, ""):
                try:
                    setattr(self, f, float(v))
                except (TypeError, ValueError):
                    setattr(self, f, None)
            else:
                setattr(self, f, None)
        self.date = _iso_date(self.date)
        if not self.id:
            seed = f"{self.date}|{self.event_type}|{self.country}|{self.location}|{self.actor1}|{self.notes[:60]}"
            self.id = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]

    @property
    def severity(self) -> str:
        f = self.fatalities
        if f >= 25 or self.event_type == "explosion/remote" and f >= 10:
            return "critical"
        if f >= 5:
            return "high"
        if f >= 1:
            return "medium"
        if self.event_type in ("battle", "drone/uas", "explosion/remote"):
            return "low"
        return "info"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["severity"] = self.severity
        return d


_TYPE_HINTS = [
    (("drone", "uav", "uas", "fpv", "loitering", "kamikaze drone"), "drone/uas"),
    (("airstrike", "shelling", "missile", "artillery", "ied", "rocket", "bomb", "explosion"), "explosion/remote"),
    (("clash", "battle", "fighting", "offensive", "assault", "armed clash"), "battle"),
    (("civilian", "massacre", "abduction", "execution"), "violence against civilians"),
    (("riot",), "riots"),
    (("protest", "demonstration"), "protests"),
    (("agreement", "withdrawal", "ceasefire", "deployment", "captured", "seized"), "strategic development"),
]


def _coerce_event_type(raw: str) -> str:
    s = (raw or "").lower()
    for keys, t in _TYPE_HINTS:
        if any(k in s for k in keys):
            return t
    return "other"


def _iso_date(s) -> str:
    if not s:
        return ""
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%d %B %Y", "%d %b %Y", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:len(fmt) + 4], fmt).date().isoformat()
        except ValueError:
            continue
    m = re.search(r"(\d{4}-\d{2}-\d{2})", s)
    return m.group(1) if m else s[:10]


# common aliases across datasets -> canonical field
_ALIASES = {
    "event_date": "date", "date_time": "date", "timestamp": "date", "pubdate": "date",
    "type": "event_type", "event": "event_type", "sub_event_type": "event_type",
    "actor1": "actor1", "actor_1": "actor1", "side_a": "actor1", "attacker": "actor1",
    "actor2": "actor2", "actor_2": "actor2", "side_b": "actor2", "target": "actor2",
    "admin1": "region", "province": "region", "state": "region", "oblast": "region",
    "location_name": "location", "place": "location", "city": "location",
    "latitude": "lat", "longitude": "lon", "lng": "lon",
    "fatalities": "fatalities", "deaths": "fatalities", "killed": "fatalities",
    "source_scale": "source", "outlet": "source", "url": "source_url", "link": "source_url",
    "notes": "notes", "description": "notes", "summary": "notes", "title": "notes",
}


def normalize(record: dict, source: str = "") -> ConflictEvent:
    fields = {"source": source} if source else {}
    for k, v in record.items():
        kl = str(k).strip().lower()
        canon = _ALIASES.get(kl, kl if kl in ConflictEvent.__dataclass_fields__ else None)
        if canon and canon in ConflictEvent.__dataclass_fields__ and v not in (None, ""):
            fields[canon] = v
    fields.pop("id", None)
    return ConflictEvent(**{k: v for k, v in fields.items()
                            if k in ConflictEvent.__dataclass_fields__})


def dedupe(events: list[ConflictEvent]) -> list[ConflictEvent]:
    seen, out = set(), []
    for e in events:
        if e.id in seen:
            continue
        seen.add(e.id)
        out.append(e)
    return out
