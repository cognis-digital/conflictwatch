"""'What's working' — a sourced, descriptive lessons-learned knowledge base.

Distilled from *open* reporting (think-tank assessments, after-action analysis, OSINT)
on how modern conflict is actually being fought, oriented toward **awareness, training,
and force protection**. Each lesson is descriptive and cited — observed trends and
defensive countermeasures, NOT targeting or weapon instructions.

Lessons live in `data/lessons.json`; this module loads, filters, and searches them.
"""

from __future__ import annotations

import json
import os

CATEGORIES = (
    "counter-uas", "ew-spectrum", "comms-c2", "survivability", "casualty-care",
    "logistics", "isr-osint", "mobility", "info-ops",
)

_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "lessons.json")


def load(path: str | None = None) -> list[dict]:
    with open(path or _DATA, encoding="utf-8") as fh:
        data = json.load(fh)
    return data.get("lessons", data) if isinstance(data, dict) else data


def query(lessons: list[dict] | None = None, *, category: str | None = None,
          keyword: str | None = None) -> list[dict]:
    items = lessons if lessons is not None else load()
    if category:
        items = [l for l in items if l.get("category") == category]
    if keyword:
        k = keyword.lower()
        items = [l for l in items if k in json.dumps(l).lower()]
    return items


def categories(lessons: list[dict] | None = None) -> dict:
    from collections import Counter
    items = lessons if lessons is not None else load()
    return dict(Counter(l.get("category", "other") for l in items).most_common())
