"""The demo scenarios are smoke tests: each must import, run, and not raise.

They run fully offline (the sanctions demo points the feed cache at the committed
offline snapshot), so this is safe and deterministic in CI.
"""

import importlib
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMOS = os.path.join(REPO_ROOT, "demos")
if DEMOS not in sys.path:
    sys.path.insert(0, DEMOS)

SCENARIOS = [
    "01_osint_analyst_situational_report",
    "02_force_protection_early_warning",
    "03_journalist_export_and_map",
    "04_ngo_lessons_and_sanctions",
    "05_collection_catalog_and_cuas",
]


@pytest.mark.parametrize("name", SCENARIOS)
def test_demo_runs(name, capsys):
    mod = importlib.import_module(name)
    result = mod.main()
    # demos either return None or an int exit code; both must be "success"
    assert result in (None, 0)
    out = capsys.readouterr().out
    assert out.strip(), f"demo {name} produced no output"


def test_run_all_exits_zero(capsys):
    run_all = importlib.import_module("run_all")
    assert run_all.main() == 0
    assert "All conflictwatch demo scenarios completed." in capsys.readouterr().out
