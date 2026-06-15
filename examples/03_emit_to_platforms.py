#!/usr/bin/env python3
"""Use case 3 — forward conflict events to STIX / Slack via cognis-connect.

Needs the optional extra:  pip install "conflictwatch[connect]"
    python examples/03_emit_to_platforms.py
"""
import json, os
from conflictwatch import sources
from conflictwatch.connect import map_record

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
events = [e.to_dict() for e in
          sources.parse("acled", open(os.path.join(ROOT, "demos", "sample_acled.csv"), encoding="utf-8").read())]

try:
    from cognis_connect import normalize, stix, notify
except ImportError:
    print("install cognis-connect for live emit: pip install 'conflictwatch[connect]'")
    raise SystemExit(0)

findings = [normalize(map_record(e), source="conflictwatch") for e in events]
bundle = stix.to_bundle(findings)
print("STIX objects:", len(bundle["objects"]))
# dry-run a Slack post (prints the request, sends nothing)
print(json.dumps(notify.send_slack(findings, "https://hooks.slack.test/x", dry_run=True))[:200])
