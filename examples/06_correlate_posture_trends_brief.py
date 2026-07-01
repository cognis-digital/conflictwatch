"""Demo: correlation, defensive I&W posture, temporal trends, and a brief — offline.

Runs the four capabilities added in 0.7.0 over the committed sample data:
  * correlate  — spatio-temporal clusters + actor co-occurrence network
  * indicators — GREEN/GUARDED/AMBER/RED posture per scope, with reasons
  * trends     — peaks, lulls, weekday profile, naive forecast
  * reports    — a markdown situational brief and an INTSUM handover

All descriptive open-source situational awareness — reported events only, no
targeting. No network, deterministic.

Usage:
    python examples/06_correlate_posture_trends_brief.py
"""

from __future__ import annotations

import os

from conflictwatch import correlate, indicators, trends, reports
from conflictwatch.sources import parse_generic_json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GEO = os.path.join(ROOT, "demos", "sample_correlation.json")
ESC = os.path.join(ROOT, "demos", "sample_escalation.json")


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return parse_generic_json(fh.read())


def main() -> int:
    geo = _load(GEO)
    esc = _load(ESC)

    print("=== correlate: spatio-temporal clusters ===")
    for c in correlate.clusters(geo):
        lo, hi = c["days"]
        print(f"  {c['size']} events / {c['fatalities']} fatalities "
              f"({lo}..{hi}, ~{c['radius_km']}km): {', '.join(c['actors'][:3])}")

    print("\n=== indicators: defensive posture per country ===")
    for p in indicators.posture(esc, scope="country"):
        print(f"  [{p['tier'].upper():<7}] {p['scope']}  (score {p['score']})")

    print("\n=== trends: temporal shape ===")
    fc = trends.forecast(esc)
    print(f"  direction={fc['direction']} slope={fc['slope_per_day']}/day; "
          f"{len(trends.peaks(esc))} peak day(s)")

    print("\n=== reports: INTSUM handover (first lines) ===")
    print("\n".join(reports.to_intsum(esc, area="EAST SECTOR").splitlines()[:6]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
