"""Demo: enrich conflict events with OFAC SDN sanctions screening — fully offline.

Runs against the committed trimmed OFAC SDN snapshot under tests/fixtures/feeds_cache,
so it needs no network. In the field you would instead:

    conflictwatch feeds update ofac-sdn          # once, while connected
    conflictwatch feeds snapshot-export sdn.tar.gz
    # sneakernet sdn.tar.gz to the air-gapped box, then:
    conflictwatch feeds snapshot-import sdn.tar.gz
    conflictwatch sanctions events.json --offline

Usage:
    python examples/04_sanctions_enrichment.py
"""

from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# point the feed cache at the committed offline snapshot
os.environ.setdefault("COGNIS_FEEDS_CACHE",
                      os.path.join(ROOT, "tests", "fixtures", "feeds_cache"))

from conflictwatch import sanctions  # noqa: E402
from conflictwatch.sources import parse_generic_json  # noqa: E402


def main() -> None:
    with open(os.path.join(ROOT, "demos", "sample_events_sanctions.json"),
              encoding="utf-8") as fh:
        events = parse_generic_json(fh.read())

    flagged = sanctions.screen_events(events, offline=True)
    print(f"{len(events)} events ingested; {len(flagged)} name an OFAC-sanctioned actor\n")
    for f in flagged:
        for m in f["matches"]:
            print(f"  [{f['date']}] {f['country']:<8} '{m['actor']}'"
                  f"  ->  OFAC SDN '{m['sdn_name']}'  ({m['sdn_type']}/{m['program']})")
    print("\nfull JSON:")
    print(json.dumps(flagged, indent=2))


if __name__ == "__main__":
    main()
