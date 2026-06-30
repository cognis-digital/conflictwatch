"""Shared helpers for the conflictwatch demo scenarios.

Every scenario runs **fully offline** off data committed in this repo:
  * ``demos/sample_acled.csv``          — a small ACLED-shaped export
  * ``demos/sample_escalation.json``    — a multi-week stream with a late surge
  * ``demos/sample_events_sanctions.json`` — events naming OFAC-listed actors

The sanctions scenario also points the data-feed cache at the committed offline
OFAC SDN snapshot under ``tests/fixtures/feeds_cache`` so it never touches the
network. No keys, no live feeds, deterministic output.
"""
from __future__ import annotations

import os
import sys

# allow `python demos/xx.py` from anywhere
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMOS = os.path.join(REPO_ROOT, "demos")

SAMPLE_ACLED = os.path.join(DEMOS, "sample_acled.csv")
SAMPLE_ESCALATION = os.path.join(DEMOS, "sample_escalation.json")
SAMPLE_SANCTIONS = os.path.join(DEMOS, "sample_events_sanctions.json")

# committed offline OFAC SDN / GDELT snapshot — lets the sanctions demo run air-gapped
OFFLINE_FEED_CACHE = os.path.join(REPO_ROOT, "tests", "fixtures", "feeds_cache")


def use_offline_feed_cache() -> None:
    """Point the data-feed layer at the committed offline snapshot (no network)."""
    os.environ.setdefault("COGNIS_FEEDS_CACHE", OFFLINE_FEED_CACHE)


def load_acled():
    from conflictwatch import sources
    with open(SAMPLE_ACLED, encoding="utf-8") as fh:
        return sources.parse("acled", fh.read())


def load_escalation():
    from conflictwatch.sources import parse_generic_json
    with open(SAMPLE_ESCALATION, encoding="utf-8") as fh:
        return parse_generic_json(fh.read())


def load_sanctions_events():
    from conflictwatch.sources import parse_generic_json
    with open(SAMPLE_SANCTIONS, encoding="utf-8") as fh:
        return parse_generic_json(fh.read())


def rule(title: str) -> None:
    print("\n" + "=" * 72)
    print(f"  {title}")
    print("=" * 72)
