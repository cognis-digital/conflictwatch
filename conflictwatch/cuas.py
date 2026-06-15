"""Counter-UAS knowledge base - query the 2024-2026 anti-drone OSINT corpus.

Loads `data/counter_uas.json` (detection, defeat, doctrine, threat trends from the
Russia-Ukraine war) and filters it by topic / keyword / confidence. Descriptive
force-protection awareness only - not weapon build, guidance, or targeting.
"""

from __future__ import annotations

import json
import os
from collections import Counter

_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "counter_uas.json")

TOPICS = (
    "fiber-optic-drones", "acoustic-detection", "rf-radar-detection", "ew-jamming",
    "interceptor-drones", "counter-shahed", "layered-cuas", "optical-ai",
    "western-cuas", "economics-adaptation",
)


def load(path: str | None = None) -> dict:
    with open(path or _DATA, encoding="utf-8") as fh:
        return json.load(fh)


def entries(path: str | None = None) -> list[dict]:
    return load(path).get("entries", [])


def query(items: list[dict] | None = None, *, topic: str | None = None,
          keyword: str | None = None, confidence: str | None = None) -> list[dict]:
    items = items if items is not None else entries()
    out = []
    for e in items:
        if topic and e.get("topic") != topic:
            continue
        if confidence and e.get("confidence") != confidence:
            continue
        if keyword and keyword.lower() not in json.dumps(e).lower():
            continue
        out.append(e)
    return out


def topics(items: list[dict] | None = None) -> dict:
    return dict(Counter(e.get("topic", "?") for e in (items if items is not None else entries())).most_common())


def systems(items: list[dict] | None = None) -> list[str]:
    """Every named system/program mentioned across the corpus (deduped, sorted)."""
    seen = {}
    for e in (items if items is not None else entries()):
        for s in e.get("systems", []):
            seen[s.lower()] = s
    return sorted(seen.values())


def stats(items: list[dict] | None = None) -> dict:
    items = items if items is not None else entries()
    return {
        "total": len(items),
        "by_topic": topics(items),
        "by_confidence": dict(Counter(e.get("confidence", "?") for e in items).most_common()),
        "unique_sources": len({s for e in items for s in e.get("sources", [])}),
        "named_systems": len(systems(items)),
    }
