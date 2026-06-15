"""Catalog (290+ sources), glossary, scraper-from-catalog, connect bridge, CLI sources."""

from __future__ import annotations

import json
import os

from conflictwatch import catalog, scrape
from conflictwatch.cli import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_catalog_size_and_shape():
    s = catalog.sources()
    assert len(s) >= 170, f"catalog should have 170+ sources, has {len(s)}"
    for e in s[:50]:
        assert e["url"].startswith("http") and e.get("name")
        assert "category" in e and "access" in e


def test_catalog_filter_and_feeds():
    uk = catalog.filter_sources(category="ukraine")
    assert uk and all(e["category"] == "ukraine" for e in uk)
    open_only = catalog.filter_sources(access="open")
    assert all(e["access"] == "open" for e in open_only)
    feeds = catalog.feeds()
    assert len(feeds) >= 40 and all(f.startswith("http") for f in feeds)


def test_catalog_has_expected_anchors():
    names = " ".join(s["name"].lower() for s in catalog.sources())
    for anchor in ("acled", "gdelt", "bellingcat", "reliefweb"):
        assert anchor in names


def test_catalog_stats():
    st = catalog.stats()
    assert st["total"] == len(catalog.sources())
    assert st["with_rss"] >= 1 and "datasets" in st["by_category"]


def test_glossary_terms_present():
    g = json.load(open(os.path.join(ROOT, "conflictwatch", "data", "glossary.json"), encoding="utf-8"))
    terms = {t["term"] for t in g["terms"]}
    for t in ("OSINT", "GEOINT", "C-UAS", "EW", "TCCC", "ACLED", "ADS-B"):
        assert t in terms
    assert all(t.get("expansion") and t.get("note") for t in g["terms"])


def test_scrape_collect_from_catalog_offline(monkeypatch):
    # don't hit the network; feed a canned RSS for any URL
    rss = ('<rss><channel><item><title>Shelling reported near Vale</title>'
           '<link>http://x/1</link><pubDate>2026-06-12</pubDate></item></channel></rss>')
    monkeypatch.setattr(scrape, "fetch_feed", lambda url, timeout=30.0: scrape.parse_feed(rss))
    monkeypatch.setattr(catalog, "feeds", lambda items=None: ["http://feed.test/a", "http://feed.test/b"])
    events = scrape.collect_from_catalog()
    assert len(events) == 2 and events[0].event_type == "explosion/remote"


def test_cli_sources_filters_and_stats(capsys):
    assert main(["sources", "--stats"]) == 0
    assert json.loads(capsys.readouterr().out)["total"] >= 170
    assert main(["sources", "--category", "datasets", "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data and all(d["category"] == "datasets" for d in data)


def test_cli_sources_feeds(capsys):
    assert main(["sources", "--has-rss", "--feeds"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out and all(u.startswith("http") for u in out)


def test_connect_map_record():
    from conflictwatch.connect import map_record
    out = map_record({"event_type": "drone/uas", "country": "X", "notes": "FPV strike",
                      "fatalities": 2, "severity": "medium", "lat": 1.0, "lon": 2.0})
    assert out["title"] and out["type"] == "drone/uas" and "X" in out["tags"]
