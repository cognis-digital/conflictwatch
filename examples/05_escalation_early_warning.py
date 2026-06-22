"""Demo: escalation early-warning over a conflict-event stream — fully offline.

Loads the committed escalation scenario (demos/sample_escalation.json) and runs the
six `conflictwatch.watch` detectors, then replays the same data "as of" an earlier date
to show how lead time appears. No network, deterministic.

Usage:
    python examples/05_escalation_early_warning.py
"""

from __future__ import annotations

import os

from conflictwatch import watch
from conflictwatch.sources import parse_generic_json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EVENTS = os.path.join(ROOT, "demos", "sample_escalation.json")


def load():
    with open(EVENTS, encoding="utf-8") as fh:
        return parse_generic_json(fh.read())


def main() -> int:
    events = load()

    print("=== early-warning AS OF the latest day (scope=country) ===")
    s = watch.summary(events, scope="country", window=7)
    print(f"{s['total_alerts']} alert(s); highest={s['highest']}; "
          f"by-severity={s['by_severity']}")
    for a in s["alerts"]:
        print(f"  [{a['severity']:<8}] {a['detector']:<16} {a['scope']}: {a['reason']}")

    print("\n=== same data REPLAYED as of 2026-06-10 (before the surge) ===")
    early = watch.detect(events, scope="country", as_of="2026-06-10",
                         min_severity="medium")
    print(f"{len(early)} medium+ alert(s) -> the gap to the critical run above is your lead time")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
