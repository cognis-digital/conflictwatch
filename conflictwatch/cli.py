"""conflictwatch CLI - ingest open conflict data, analyze it, consult lessons.

    conflictwatch ingest --source acled --from-file acled.csv --out events.json
    conflictwatch fetch-gdelt --out events.json          # latest open GDELT export
    conflictwatch scrape --out osint.json                # OSINT situational feeds
    conflictwatch analyze events.json --window 7
    conflictwatch lessons --category counter-uas
    conflictwatch report events.json                     # full situational summary
"""

from __future__ import annotations

import argparse
import json
import sys

from conflictwatch import (TOOL_NAME, TOOL_VERSION, analyze, catalog,
                           cuas as cuas_kb, lessons as lessons_kb, scrape, sources)
from conflictwatch.events import dedupe


def _write(events, path):
    data = [e.to_dict() for e in events]
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        print(f"wrote {len(data)} events to {path}")
    else:
        print(json.dumps(data, indent=2))


def _load_events(path):
    with open(path, encoding="utf-8") as fh:
        return sources.parse_generic_json(fh.read())


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog=TOOL_NAME, description=__doc__.splitlines()[0])
    p.add_argument("--version", action="version", version=f"{TOOL_NAME} {TOOL_VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True)

    ig = sub.add_parser("ingest", help="parse an open conflict dataset into events")
    ig.add_argument("--source", required=True, choices=["acled", "gdelt", "ucdp", "json"])
    ig.add_argument("--from-file", required=True)
    ig.add_argument("--out", default=None)

    fg = sub.add_parser("fetch-gdelt", help="pull the latest open GDELT events export")
    fg.add_argument("--out", default=None)

    sc = sub.add_parser("scrape", help="collect OSINT situational feeds (RSS/Atom)")
    sc.add_argument("--feeds", nargs="*", default=None)
    sc.add_argument("--out", default=None)

    an = sub.add_parser("analyze", help="situational summary over an events file")
    an.add_argument("input")
    an.add_argument("--window", type=int, default=7)
    an.add_argument("--format", choices=["table", "json"], default="table")

    ex = sub.add_parser("export", help="export events as STIX 2.1 or GeoJSON (native, no deps)")
    ex.add_argument("input")
    ex.add_argument("--to", choices=["stix", "geojson"], default="geojson")
    ex.add_argument("-o", "--output", default=None, help="write to file instead of stdout")

    le = sub.add_parser("lessons", help="query the 'what's working' lessons KB")
    le.add_argument("--category", default=None, choices=list(lessons_kb.CATEGORIES))
    le.add_argument("--keyword", default=None)
    le.add_argument("--format", choices=["table", "json"], default="table")

    rp = sub.add_parser("report", help="full situational report (summary + lessons hint)")
    rp.add_argument("input")
    rp.add_argument("--window", type=int, default=7)

    cu = sub.add_parser("cuas", help="query the counter-UAS / anti-drone knowledge base (2024-2026)")
    cu.add_argument("--topic", default=None, choices=list(cuas_kb.TOPICS))
    cu.add_argument("--keyword", default=None)
    cu.add_argument("--confidence", default=None, choices=["high", "medium", "low"])
    cu.add_argument("--systems", action="store_true", help="list every named system/program")
    cu.add_argument("--stats", action="store_true")
    cu.add_argument("--format", choices=["table", "json"], default="table")

    so = sub.add_parser("sources", help="query the 290+ open conflict/OSINT source catalog")
    so.add_argument("--category", default=None)
    so.add_argument("--type", default=None)
    so.add_argument("--access", default=None, choices=["open", "registration", "paid"])
    so.add_argument("--region", default=None)
    so.add_argument("--keyword", default=None)
    so.add_argument("--has-rss", action="store_true")
    so.add_argument("--feeds", action="store_true", help="print just the RSS feed URLs")
    so.add_argument("--stats", action="store_true")
    so.add_argument("--format", choices=["table", "json"], default="table")

    args = p.parse_args(argv)

    try:
        if args.cmd == "ingest":
            with open(args.from_file, encoding="utf-8") as fh:
                events = dedupe(sources.parse(args.source, fh.read()))
            _write(events, args.out)
        elif args.cmd == "fetch-gdelt":
            _write(dedupe(sources.fetch_gdelt_latest()), args.out)
        elif args.cmd == "scrape":
            _write(dedupe(scrape.collect(args.feeds)), args.out)
        elif args.cmd == "analyze":
            s = analyze.summary(_load_events(args.input), args.window)
            if args.format == "json":
                print(json.dumps(s, indent=2))
            else:
                _print_summary(s)
        elif args.cmd == "export":
            from conflictwatch import intel
            events = _load_events(args.input)
            text = intel.export(events, args.to)
            if args.output:
                with open(args.output, "w", encoding="utf-8") as fh:
                    fh.write(text if text.endswith("\n") else text + "\n")
                print(f"wrote {args.to} export ({len(events)} events) to {args.output}",
                      file=sys.stderr)
            else:
                print(text)
        elif args.cmd == "lessons":
            items = lessons_kb.query(category=args.category, keyword=args.keyword)
            if args.format == "json":
                print(json.dumps(items, indent=2))
            else:
                _print_lessons(items)
        elif args.cmd == "cuas":
            if args.stats:
                print(json.dumps(cuas_kb.stats(), indent=2)); return 0
            if args.systems:
                print("\n".join(cuas_kb.systems())); return 0
            items = cuas_kb.query(topic=args.topic, keyword=args.keyword, confidence=args.confidence)
            if args.format == "json":
                print(json.dumps(items, indent=2))
            else:
                print(f"{len(items)} counter-UAS entr(ies):")
                for e in items:
                    print(f"\n[{e['topic']}] {e['title']}  ({e.get('date','')}, {e.get('confidence','')})")
                    if e.get("summary"):
                        print(f"  {e['summary'][:200]}")
                    if e.get("countermeasures"):
                        print("  defense: " + "; ".join(e["countermeasures"][:2])[:200])
        elif args.cmd == "sources":
            if args.stats:
                print(json.dumps(catalog.stats(), indent=2)); return 0
            items = catalog.filter_sources(category=args.category, type=args.type,
                                           access=args.access, region=args.region,
                                           keyword=args.keyword,
                                           has_rss=True if args.has_rss else None)
            if args.feeds:
                print("\n".join(catalog.feeds(items))); return 0
            if args.format == "json":
                print(json.dumps(items, indent=2))
            else:
                print(f"{len(items)} source(s):")
                for s in items:
                    rss = " [rss]" if s.get("rss") else ""
                    print(f"  [{s.get('category',''):12}] {s.get('name','')}{rss}\n      {s.get('url','')}")
        elif args.cmd == "report":
            s = analyze.summary(_load_events(args.input), args.window)
            _print_summary(s)
            print("\nRelevant lessons (awareness):")
            seen = set()
            for et in s["by_type"]:
                cat = {"drone/uas": "counter-uas", "explosion/remote": "survivability",
                       "battle": "survivability"}.get(et)
                if cat and cat not in seen:
                    seen.add(cat)
                    for l in lessons_kb.query(category=cat)[:1]:
                        print(f"  - [{l['category']}] {l['title']}: {l['insight'][:100]}")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _print_summary(s):
    lo, hi = s["date_range"]
    print(f"CONFLICTWATCH situational summary  ({lo} .. {hi})")
    print(f"  events: {s['total_events']}   fatalities: {s['total_fatalities']}")
    print(f"  severity: {s['by_severity']}")
    t = s["trend"]
    arrow = "UP" if t["escalating"] else "flat/down"
    print(f"  trend ({arrow}): recent {t['recent']} vs prior {t['prior']} ({t['change_pct']:+}%)")
    print("  hotspots:")
    for h in s["hotspots"]:
        print(f"    {h['country']:<20} {h['area']:<22} events={h['events']:<4} fatalities={h['fatalities']}")
    print("  top actors:")
    for a in s["top_actors"]:
        print(f"    {a['actor']:<32} events={a['events']:<4} fatalities={a['fatalities']}")


def _print_lessons(items):
    print(f"{len(items)} lesson(s):")
    for l in items:
        print(f"\n[{l['category']}] {l['title']}  ({l.get('confidence','?')})")
        print(f"  insight: {l['insight']}")
        if l.get("indicators"):
            print("  indicators: " + "; ".join(l["indicators"]))
        if l.get("countermeasures"):
            print("  countermeasures: " + "; ".join(l["countermeasures"]))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
