"""OSINT collection — pull conflict reporting from open RSS/Atom feeds.

Aggregates public situational-reporting feeds (think-tanks, monitors, open trackers) into
ConflictEvents for awareness. Stdlib XML parsing; polite (identifies itself, times out).
Respect each publisher's terms and robots; this reads public feeds, nothing covert.

Curated open feeds (situational analysis / monitoring), edit to taste:
"""

from __future__ import annotations

import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from conflictwatch.events import ConflictEvent, normalize

UA = "conflictwatch-osint/0.1 (+https://github.com/cognis-digital/conflictwatch)"

# public situational-awareness / conflict-monitoring feeds (open RSS)
DEFAULT_FEEDS = [
    "https://www.understandingwar.org/feeds/all.xml",          # ISW campaign assessments
    "https://acleddata.com/feed/",                              # ACLED analysis
    "https://reliefweb.int/updates/rss.xml",                   # OCHA ReliefWeb situation reports
    "https://www.bellingcat.com/feed/",                        # open-source investigations
]


def fetch_feed(url: str, timeout: float = 30.0) -> list[dict]:
    """Return raw items [{title, link, published, summary}] from an RSS/Atom feed."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        xml = r.read().decode("utf-8", "replace")
    return parse_feed(xml)


def parse_feed(xml: str) -> list[dict]:
    items: list[dict] = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return items

    def text(el, *names):
        for n in names:
            for child in el.iter():
                if child.tag.rsplit("}", 1)[-1].lower() == n:
                    if child.text and child.text.strip():
                        return child.text.strip()
                    if child.get("href"):
                        return child.get("href")
        return ""

    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1].lower()
        if tag in ("item", "entry"):
            items.append({
                "title": text(el, "title"),
                "link": text(el, "link"),
                "published": text(el, "pubdate", "published", "updated", "date"),
                "summary": text(el, "description", "summary", "content"),
            })
    return items


def items_to_events(items: list[dict], source: str = "OSINT feed") -> list[ConflictEvent]:
    out = []
    for it in items:
        out.append(normalize({
            "date": it.get("published", ""),
            "notes": it.get("title", "") or it.get("summary", "")[:200],
            "source_url": it.get("link", ""),
            "event_type": it.get("title", "") + " " + it.get("summary", ""),  # coerced by heuristics
        }, source=source))
    return out


def collect(feeds: list[str] | None = None, timeout: float = 30.0) -> list[ConflictEvent]:
    """Fetch all feeds and return ConflictEvents (best-effort; skips unreachable feeds)."""
    events: list[ConflictEvent] = []
    for url in (feeds or DEFAULT_FEEDS):
        try:
            events.extend(items_to_events(fetch_feed(url, timeout), source=_host(url)))
        except Exception:
            continue
    return events


def collect_from_catalog(category: str | None = None, timeout: float = 30.0) -> list[ConflictEvent]:
    """Collect from every RSS feed in the source catalog (optionally one category)."""
    from conflictwatch import catalog
    items = catalog.filter_sources(category=category) if category else None
    return collect(catalog.feeds(items), timeout)


def _host(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc or url
    except Exception:
        return url
