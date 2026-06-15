"""Native cognis-connect emit for conflictwatch - forward events to any platform.

Maps ConflictEvents to the canonical Finding and forwards via cognis-connect
(STIX/MISP/Sigma/Splunk/Elastic/Slack/webhook). Soft dependency:
    pip install "git+https://github.com/cognis-digital/cognis-connect.git"

    conflictwatch ingest --source acled --from-file a.csv | python -m conflictwatch.connect --to slack
"""

from __future__ import annotations

import argparse
import json
import sys

SOURCE = "conflictwatch"


def map_record(rec: dict) -> dict:
    out = dict(rec)
    out["title"] = (rec.get("notes") or rec.get("event_type") or "conflict event")[:120]
    out["type"] = rec.get("event_type", "conflict-event")
    out.setdefault("severity", rec.get("severity", "info"))
    out["description"] = (f"{rec.get('event_type','')} in {rec.get('country','')} "
                          f"({rec.get('location','')}) - {rec.get('fatalities',0)} fatalities").strip()
    tags = [t for t in [rec.get("country"), rec.get("actor1"), rec.get("event_type")] if t]
    out["tags"] = (rec.get("tags") or []) + tags
    if rec.get("lat") is not None:
        out["lat"] = rec["lat"]
    if rec.get("lon") is not None:
        out["lon"] = rec["lon"]
    return out


def _findings(text: str):
    from cognis_connect.findings import normalize
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("events") or data.get("findings") or [data]
    return [normalize(map_record(r), source=SOURCE) for r in data if isinstance(r, dict)]


def emit_main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="conflictwatch-emit",
                                description="forward conflictwatch events to a platform via cognis-connect")
    p.add_argument("--to", required=True,
                   choices=["stix", "taxii", "misp", "sigma", "splunk", "elastic",
                            "slack", "discord", "webhook", "brief", "findings"])
    p.add_argument("input", nargs="?", default="-")
    p.add_argument("--url", default=None)
    p.add_argument("--token", default=None)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    try:
        from cognis_connect import misp, notify, sigma, siem, stix, edgemesh
    except ImportError:
        print("needs cognis-connect: pip install "
              "git+https://github.com/cognis-digital/cognis-connect.git", file=sys.stderr)
        return 1
    text = sys.stdin.read() if a.input == "-" else open(a.input, encoding="utf-8").read()
    fs = _findings(text)
    try:
        if a.to == "stix":
            print(json.dumps(stix.to_bundle(fs), indent=2))
        elif a.to == "taxii":
            print(json.dumps(stix.push_taxii(fs, a.url, token=a.token, dry_run=a.dry_run), indent=2))
        elif a.to == "misp":
            print(json.dumps(misp.push(fs, a.url, a.token or "", dry_run=a.dry_run) if a.url
                             else misp.to_event(fs), indent=2))
        elif a.to == "sigma":
            print(sigma.to_rules(fs))
        elif a.to == "splunk":
            print(json.dumps(siem.send_splunk(fs, a.url, a.token or "", dry_run=a.dry_run), indent=2))
        elif a.to == "elastic":
            print(json.dumps(siem.send_elastic(fs, a.url, token=a.token, dry_run=a.dry_run), indent=2))
        elif a.to == "slack":
            print(json.dumps(notify.send_slack(fs, a.url, dry_run=a.dry_run), indent=2))
        elif a.to == "discord":
            print(json.dumps(notify.send_discord(fs, a.url, dry_run=a.dry_run), indent=2))
        elif a.to == "webhook":
            print(json.dumps(siem.send_webhook(fs, a.url, token=a.token, dry_run=a.dry_run), indent=2))
        elif a.to == "brief":
            print(edgemesh.summarize(fs, base=a.url))
        elif a.to == "findings":
            from cognis_connect.findings import dump
            print(dump(fs))
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(emit_main())
