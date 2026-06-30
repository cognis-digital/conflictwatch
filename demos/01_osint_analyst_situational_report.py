"""Scenario 1 - OSINT analysts: build a situational picture from an open export.

The analyst's first question on any feed is *where is the activity, who is driving
it, and is it getting worse?* This demo ingests a small ACLED-shaped export, then
asks `conflictwatch.analyze` for the hotspots, the most-active actors, the event
mix, and the escalation trend — the four things that go at the top of a brief.

Pure offline, deterministic. This is the descriptive snapshot; demo 2 (force
protection) shows the *delta* that early-warning adds on top.
"""
from _common import load_acled, rule

from conflictwatch import analyze


def main() -> None:
    rule("OSINT ANALYST  -  situational summary from an open ACLED export")

    events = load_acled()
    print(f"\nIngested {len(events)} events from demos/sample_acled.csv (ACLED-shaped).")

    s = analyze.summary(events, window_days=7)
    lo, hi = s["date_range"]
    print(f"\nPicture {lo} .. {hi}:  {s['total_events']} events, "
          f"{s['total_fatalities']} reported fatalities")
    print(f"  severity mix : {s['by_severity']}")
    print(f"  event types  : {s['by_type']}")

    t = s["trend"]
    arrow = "ESCALATING" if t["escalating"] else "flat / cooling"
    print(f"  trend ({arrow}): recent {t['recent']} vs prior {t['prior']} "
          f"({t['change_pct']:+}% window-over-window)")

    print("\nHotspots (where the activity concentrates):")
    for h in s["hotspots"]:
        print(f"    {h['country']:<14} {h['area']:<16} "
              f"events={h['events']:<3} fatalities={h['fatalities']}")

    print("\nMost-active actors:")
    for a in s["top_actors"]:
        print(f"    {a['actor']:<28} events={a['events']:<3} fatalities={a['fatalities']}")

    print("\nThat is the snapshot an analyst leads a brief with - all from open data,")
    print("all on local hardware.  See demo 2 for what is *changing*.")


if __name__ == "__main__":
    main()
