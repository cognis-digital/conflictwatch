"""Scenario 4 - NGOs & humanitarian staff: who is involved, and how to stay safe.

Two questions dominate humanitarian situational awareness in an active area:
  1. *Are any of the armed actors in our area legally sanctioned?* - it changes
     duty-of-care, partner vetting, and what aid can lawfully flow.
  2. *What's working for protection right now?* - the descriptive, defensive
     lessons that keep field staff and convoys safer.

This demo answers both, fully offline. It screens an event stream against the
**OFAC SDN** list (served from the committed offline snapshot, so it runs
air-gapped) and then pulls the matching awareness lessons from the
"what's working" KB. Descriptive / protective only - no targeting.
"""
from _common import load_sanctions_events, rule, use_offline_feed_cache

from conflictwatch import lessons, sanctions


def main() -> None:
    rule("NGO / HUMANITARIAN  -  sanctioned-actor screening + protection lessons")

    use_offline_feed_cache()  # serve OFAC SDN from the committed offline snapshot
    events = load_sanctions_events()
    print(f"\n{len(events)} events in the area of operations.")

    flagged = sanctions.screen_events(events, offline=True)
    print(f"\nOFAC SDN screening: {len(flagged)} of {len(events)} events name a "
          "sanctioned actor:")
    for f in flagged:
        print(f"\n  [{f['date']}] {f.get('country',''):<10} event {f['event_id']}")
        for m in f["matches"]:
            tag = "STRONG" if m["strong"] else f"{m['shared_terms']} shared terms"
            prog = m["program"] or "(program n/a)"
            print(f"      actor '{m['actor']}'  ->  SDN '{m['sdn_name']}'  "
                  f"({m['sdn_type']}/{prog}) [{tag}]")
    print("\n  A sanctioned counterpart changes duty-of-care, vetting, and lawful aid flow.")

    # --- protection lessons keyed to what's in the stream ------------------ #
    print("\nProtection lessons ('what's working') for the event types present:")
    cats = {"drone/uas": "counter-uas", "explosion/remote": "survivability"}
    seen = set()
    for e in events:
        cat = cats.get(e.event_type)
        if cat and cat not in seen:
            seen.add(cat)
            for l in lessons.query(category=cat)[:1]:
                print(f"\n  [{l['category']}] {l['title']}")
                print(f"      insight: {l['insight'][:140]}")
                if l.get("countermeasures"):
                    print("      defense: " + "; ".join(l["countermeasures"][:2])[:140])

    print("\nEverything above ran with zero network - field-deployable on edge gear.")


if __name__ == "__main__":
    main()
