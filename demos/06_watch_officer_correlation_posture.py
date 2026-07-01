"""Scenario 6 - watch officer: correlate the picture, read the posture, brief it.

A duty officer on shift does three things with the day's reporting: (1) find what
GOES TOGETHER - the events that cluster in place and time into a single push - so a
scattered feed reads as coherent activity; (2) read the DEFENSIVE POSTURE per area
so the team knows how alert to be and why; and (3) hand off a readable BRIEF.

This demo runs all three new capabilities, fully offline off committed sample data:
  * correlate.clusters + actor_network  -> the coordinated activity + belligerent graph
  * indicators.posture                   -> GREEN/GUARDED/AMBER/RED with reasons
  * trends.peaks/forecast                -> the temporal shape and a naive projection
  * reports.to_intsum                    -> a terse handover brief

Everything is descriptive open-source situational awareness - reported events only,
no targeting, no tasking. Deterministic; no network, no keys.
"""
from _common import load_correlation, load_escalation, rule

from conflictwatch import correlate, indicators, trends, reports


def main() -> None:
    rule("WATCH OFFICER  -  correlate -> posture -> brief")

    geo = load_correlation()
    print(f"\nLoaded {len(geo)} geolocated events from demos/sample_correlation.json")

    # --- 1) what goes together: spatio-temporal clusters -------------------- #
    cls = correlate.clusters(geo, radius_km=50, max_day_gap=3)
    print(f"\nCORRELATE - {len(cls)} spatio-temporal cluster(s) "
          "(events near in BOTH place and time):")
    for c in cls:
        lo, hi = c["days"]
        print(f"  - {c['size']} events / {c['fatalities']} fatalities, {lo}..{hi} "
              f"(~{c['radius_km']}km): {', '.join(c['actors'][:3])}")

    net = correlate.actor_network(geo)
    print(f"\n  belligerent graph: {len(net['nodes'])} actors, {len(net['edges'])} edges")
    for e in net["edges"][:3]:
        print(f"    {e['source']} <-> {e['target']}  (weight {e['weight']})")

    # --- 2) defensive posture per area -------------------------------------- #
    esc = load_escalation()
    s = indicators.summary(esc, scope="country", window=7)
    print(f"\nPOSTURE - highest={s['highest'].upper()}  "
          f"by-tier={ {k.upper(): v for k, v in s['by_tier'].items()} }")
    for p in s["postures"]:
        print(f"  [{p['tier'].upper():<7}] {p['scope']}  (score {p['score']})")
        for adv in p["advisories"][:2]:
            print(f"      - {adv}")

    # --- 3) temporal shape + a readable handover brief ---------------------- #
    fc = trends.forecast(esc, horizon=7)
    pk = trends.peaks(esc)
    print(f"\nTRENDS - direction={fc['direction']} (slope {fc['slope_per_day']}/day), "
          f"{len(pk)} peak day(s) flagged")

    print("\nBRIEF (INTSUM handover):")
    print("-" * 60)
    print(reports.to_intsum(esc, window=7, area="EAST SECTOR"))


if __name__ == "__main__":
    main()
