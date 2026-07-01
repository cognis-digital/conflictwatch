"""Run every conflictwatch demo scenario end to end.

    python demos/run_all.py

Each scenario is independent and loads its own committed sample data, so they can
be run in any order or on their own. Everything runs fully offline - no network,
no API keys - and exits 0, so this doubles as a smoke test.
"""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SCENARIOS = [
    "01_osint_analyst_situational_report",
    "02_force_protection_early_warning",
    "03_journalist_export_and_map",
    "04_ngo_lessons_and_sanctions",
    "05_collection_catalog_and_cuas",
    "06_watch_officer_correlation_posture",
]


def main() -> int:
    for name in SCENARIOS:
        mod = importlib.import_module(name)
        mod.main()
    print("\n" + "=" * 72)
    print("  All conflictwatch demo scenarios completed.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
