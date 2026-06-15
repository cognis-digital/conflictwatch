#!/usr/bin/env python3
"""Use case 2 — browse the 290+ source catalog and pull the OSINT RSS feeds.

    python examples/02_browse_catalog.py
"""
from conflictwatch import catalog

print("catalog stats:", catalog.stats()["total"], "sources,",
      catalog.stats()["with_rss"], "with RSS")
print("\ncategories:", catalog.categories())

print("\nopen Ukraine trackers:")
for s in catalog.filter_sources(category="ukraine", access="open")[:8]:
    print(f"  {s['name']} -> {s['url']}")

print("\nall GEOINT/imagery tools:")
for s in catalog.filter_sources(category="geoint")[:8]:
    print(f"  {s['name']}")

print(f"\n{len(catalog.feeds())} RSS feeds available to scrape, e.g.:")
for f in catalog.feeds()[:6]:
    print("  ", f)
