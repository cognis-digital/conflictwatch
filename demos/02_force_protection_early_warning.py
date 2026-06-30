"""Scenario 2 - force protection: early-warning that ranks what is CHANGING.

For force protection the biggest number is rarely the useful one - the place that
was already loud yesterday is known. What matters is the quiet sector taking its
first shelling, the new unit that just appeared, the front that is widening. That
is the *delta*, and `conflictwatch.watch` ranks it with six deterministic,
auditable detectors over a recent window vs the trailing baseline.

This demo runs the detectors over a multi-week stream that surges late, then
REPLAYS the same data "as of" an earlier day to show the lead time you would have
had. Every alert carries severity, score, and a plain-language reason. Offline,
deterministic - no models, no network.
"""
from _common import load_escalation, rule

from conflictwatch import watch


def main() -> None:
    rule("FORCE PROTECTION  -  escalation early-warning (rank the delta, not the volume)")

    events = load_escalation()
    print(f"\nLoaded {len(events)} events from demos/sample_escalation.json "
          "(weeks of baseline + a late surge).")

    # --- 1) where do we stand AS OF the latest day -------------------------- #
    s = watch.summary(events, scope="country", window=7, baseline_windows=4)
    print(f"\nAS OF the latest day (scope=country, window=7d, baseline=4x):")
    print(f"  {s['total_alerts']} alert(s)  highest={s['highest']}  "
          f"by-severity={s['by_severity']}")
    for a in s["alerts"]:
        print(f"    [{a['severity']:<8}] {a['detector']:<16} {a['scope']}")
        print(f"         {a['reason']}  (score {a['score']})")

    # --- 2) replay the SAME data at an earlier day to expose lead time ------- #
    print("\nREPLAY -- same data evaluated as of 2026-06-10 (before the surge):")
    early = watch.detect(events, scope="country", window=7,
                         as_of="2026-06-10", min_severity="medium")
    print(f"  {len(early)} medium+ alert(s) already firing on 2026-06-10:")
    for a in early:
        print(f"    [{a['severity']:<8}] {a['detector']:<16} {a['reason']}")
    print("\n  The gap between this earlier read and the critical run above")
    print("  is the lead time the early-warning bought you to act.")


if __name__ == "__main__":
    main()
