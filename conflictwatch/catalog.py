"""The source catalog - 290+ open conflict/OSINT sources, filterable.

Loads `data/sources.json` (datasets, trackers, think-tanks, humanitarian, GEOINT, flight/
maritime/SDR tracking, drone/EW, news, OSINT tooling, and curated bookmarks). Use it to
discover sources, drive the scraper from the catalog's RSS feeds, or render SOURCES.md.
"""

from __future__ import annotations

import json
import os
from collections import Counter

_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "sources.json")


def load(path: str | None = None) -> dict:
    with open(path or _DATA, encoding="utf-8") as fh:
        return json.load(fh)


def sources(path: str | None = None) -> list[dict]:
    return load(path).get("sources", [])


def filter_sources(items: list[dict] | None = None, *, category: str | None = None,
                   type: str | None = None, access: str | None = None,
                   region: str | None = None, has_rss: bool | None = None,
                   keyword: str | None = None) -> list[dict]:
    items = items if items is not None else sources()
    out = []
    for s in items:
        if category and s.get("category") != category:
            continue
        if type and s.get("type") != type:
            continue
        if access and s.get("access") != access:
            continue
        if region and region.lower() not in (s.get("region", "") or "").lower():
            continue
        if has_rss is not None and bool(s.get("rss")) != has_rss:
            continue
        if keyword and keyword.lower() not in json.dumps(s).lower():
            continue
        out.append(s)
    return out


def feeds(items: list[dict] | None = None) -> list[str]:
    """Every source that exposes an RSS/Atom feed (drives scrape.collect)."""
    return [s["rss"] for s in (items if items is not None else sources()) if s.get("rss")]


def categories(items: list[dict] | None = None) -> dict:
    return dict(Counter(s.get("category", "?") for s in (items if items is not None else sources())).most_common())


def stats(items: list[dict] | None = None) -> dict:
    items = items if items is not None else sources()
    return {
        "total": len(items),
        "with_rss": sum(1 for s in items if s.get("rss")),
        "by_category": categories(items),
        "by_type": dict(Counter(s.get("type", "?") for s in items).most_common()),
        "by_access": dict(Counter(s.get("access", "?") for s in items).most_common()),
    }
