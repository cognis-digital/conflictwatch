#!/usr/bin/env python3
"""Use case 1 — ingest an ACLED export and print a situational report.

    python examples/01_ingest_and_report.py
"""
import os
from conflictwatch import sources, analyze, lessons

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
events = sources.parse("acled", open(os.path.join(ROOT, "demos", "sample_acled.csv"), encoding="utf-8").read())

s = analyze.summary(events)
print(f"{s['total_events']} events, {s['total_fatalities']} fatalities, "
      f"{s['date_range'][0]}..{s['date_range'][1]}")
print("escalating:", s["trend"]["escalating"], s["trend"])
print("\nhotspots:")
for h in s["hotspots"]:
    print(f"  {h['country']} / {h['area']}: {h['events']} events, {h['fatalities']} fatalities")

# pull relevant lessons for the dominant event types
if s["by_type"].get("drone/uas"):
    print("\ncounter-UAS awareness:")
    for l in lessons.query(category="counter-uas")[:2]:
        print(f"  - {l['title']}: {l['insight']}")
