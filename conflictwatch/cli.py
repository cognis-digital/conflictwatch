"""conflictwatch CLI - ingest open conflict data, analyze it, consult lessons.

    conflictwatch ingest --source acled --from-file acled.csv --out events.json
    conflictwatch fetch-gdelt --out events.json          # latest open GDELT export
    conflictwatch scrape --out osint.json                # OSINT situational feeds
    conflictwatch analyze events.json --window 7
    conflictwatch lessons --category counter-uas
    conflictwatch report events.json                     # full situational summary
    conflictwatch feeds list                             # edge data-feed catalog
    conflictwatch feeds update ofac-sdn                  # fetch + cache a feed
    conflictwatch feeds get gdelt --offline              # re-serve from cache (air-gap)
    conflictwatch sanctions events.json --offline        # flag OFAC-sanctioned actors
"""

from __future__ import annotations

import argparse
import json
import sys

# Edge / air-gap data feeds this repo consumes (ids from the bundled catalog).
RELEVANT_FEEDS = ("gdelt", "ofac-sdn")

from conflictwatch import (TOOL_NAME, TOOL_VERSION, analyze, catalog,
                           cuas as cuas_kb, lessons as lessons_kb, scrape, sources,
                           watch as watch_mod, correlate as correlate_mod,
                           indicators as indicators_mod, trends as trends_mod,
                           reports as reports_mod)
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

    wt = sub.add_parser("watch",
                        help="escalation early-warning: rank what is CHANGING (spikes, "
                             "trends, new actors, geo-spread, lethality shifts)")
    wt.add_argument("input")
    wt.add_argument("--scope", choices=["country", "region", "location", "global"],
                    default="country")
    wt.add_argument("--window", type=int, default=7, help="recent-window length (days)")
    wt.add_argument("--baseline-windows", type=int, default=4,
                    help="how many windows of history form the baseline")
    wt.add_argument("--as-of", default=None,
                    help="ISO date to evaluate at (replay early-warning for a past day)")
    wt.add_argument("--min-severity", default="info",
                    choices=list(watch_mod.SEVERITIES))
    wt.add_argument("--detector", default=None, choices=list(watch_mod.DETECTORS),
                    help="show only alerts from one detector")
    wt.add_argument("--format", choices=["table", "json"], default="table")

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

    fd = sub.add_parser("feeds", help="edge/air-gap data feeds (OFAC SDN, GDELT) — list/update/get")
    fdsub = fd.add_subparsers(dest="feeds_cmd", required=True)
    fdsub.add_parser("list", help="list the data feeds this repo consumes")
    fdu = fdsub.add_parser("update", help="fetch + cache a feed (online)")
    fdu.add_argument("feed", choices=list(RELEVANT_FEEDS))
    fdg = fdsub.add_parser("get", help="print a feed (cached/fetched; --offline = cache only)")
    fdg.add_argument("feed", choices=list(RELEVANT_FEEDS))
    fdg.add_argument("--offline", action="store_true")
    fde = fdsub.add_parser("snapshot-export", help="tar the feed cache for air-gap transfer")
    fde.add_argument("path")
    fdi = fdsub.add_parser("snapshot-import", help="load an air-gap feed snapshot into the cache")
    fdi.add_argument("path")

    sa = sub.add_parser("sanctions",
                        help="cross-reference event actors against the OFAC SDN list")
    sa.add_argument("input")
    sa.add_argument("--offline", action="store_true",
                    help="serve the OFAC SDN feed from cache only (edge / air-gap)")
    sa.add_argument("--format", choices=["table", "json"], default="table")

    co = sub.add_parser("correlate",
                        help="find structure: spatio-temporal clusters, actor "
                             "network, type co-occurrence, coordinated days")
    co.add_argument("input")
    co.add_argument("--mode", default="clusters",
                    choices=["clusters", "actor-network", "cooccurrence",
                             "coordinated", "all"])
    co.add_argument("--radius-km", type=float, default=50.0,
                    help="cluster radius (clusters mode)")
    co.add_argument("--max-day-gap", type=int, default=3,
                    help="max day gap to join a cluster (clusters mode)")
    co.add_argument("--min-size", type=int, default=3, help="min cluster size")
    co.add_argument("--format", choices=["table", "json"], default="table")

    po = sub.add_parser("posture",
                        help="defensive I&W posture per scope (GREEN/GUARDED/"
                             "AMBER/RED) from tempo, lethality, escalation, "
                             "drone/UAS share, geo-spread")
    po.add_argument("input")
    po.add_argument("--scope", choices=["country", "region", "location", "global"],
                    default="country")
    po.add_argument("--window", type=int, default=7)
    po.add_argument("--baseline-windows", type=int, default=4)
    po.add_argument("--as-of", default=None,
                    help="ISO date to evaluate at (replay a past day)")
    po.add_argument("--format", choices=["table", "json"], default="table")

    tr = sub.add_parser("trends",
                        help="temporal analytics: moving average, peaks, lulls, "
                             "weekday profile, naive trend forecast")
    tr.add_argument("input")
    tr.add_argument("--metric", choices=["events", "fatalities"], default="events")
    tr.add_argument("--window", type=int, default=7, help="moving-average window")
    tr.add_argument("--horizon", type=int, default=7, help="forecast horizon (days)")
    tr.add_argument("--format", choices=["table", "json"], default="table")

    br = sub.add_parser("brief",
                        help="human-readable report: markdown / csv / kml / intsum")
    br.add_argument("input")
    br.add_argument("--to", choices=list(reports_mod.FORMATS), default="markdown")
    br.add_argument("--window", type=int, default=7)
    br.add_argument("-o", "--output", default=None,
                    help="write to file instead of stdout")

    args = p.parse_args(argv)

    try:
        if args.cmd == "feeds":
            return _cmd_feeds(args)
        if args.cmd == "sanctions":
            return _cmd_sanctions(args)
        if args.cmd == "correlate":
            return _cmd_correlate(args)
        if args.cmd == "posture":
            return _cmd_posture(args)
        if args.cmd == "trends":
            return _cmd_trends(args)
        if args.cmd == "brief":
            return _cmd_brief(args)
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
        elif args.cmd == "watch":
            return _cmd_watch(args)
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


def _cmd_feeds(args) -> int:
    from conflictwatch import datafeeds as df
    catalog = {"feeds": [f for f in df.list_feeds() if f["id"] in RELEVANT_FEEDS]}
    if args.feeds_cmd == "list":
        print(f"{len(catalog['feeds'])} edge data feed(s) consumed by conflictwatch:")
        for f in catalog["feeds"]:
            age = df.cached_age_hours(f["id"])
            fresh = "uncached" if age is None else f"{age:.1f}h old"
            print(f"  {f['id']:10} [{fresh:>9}]  {f['name']}\n      {f['url']}")
        return 0
    if args.feeds_cmd == "update":
        pth = df.update(args.feed, catalog=catalog)
        print(f"updated {args.feed} -> {pth} ({pth.stat().st_size} bytes)")
        return 0
    if args.feeds_cmd == "get":
        data = df.get(args.feed, offline=args.offline, catalog=catalog)
        print(json.dumps(data, indent=2)[:4000] if isinstance(data, (dict, list))
              else str(data)[:4000])
        return 0
    if args.feeds_cmd == "snapshot-export":
        print(f"exported {df.snapshot_export(args.path)} feed(s) -> {args.path}")
        return 0
    if args.feeds_cmd == "snapshot-import":
        print(f"imported {df.snapshot_import(args.path)} feed(s) from {args.path}")
        return 0
    return 1


_SEV_MARK = {"critical": "!!!", "high": "!! ", "medium": "!  ",
             "low": ".  ", "info": "   "}


def _cmd_watch(args) -> int:
    events = _load_events(args.input)
    s = watch_mod.summary(
        events, scope=args.scope, window=args.window,
        baseline_windows=args.baseline_windows, as_of=args.as_of,
        min_severity=args.min_severity)
    alerts = s["alerts"]
    if args.detector:
        alerts = [a for a in alerts if a["detector"] == args.detector]
    if args.format == "json":
        out = dict(s)
        out["alerts"] = alerts
        out["top_alert"] = alerts[0] if alerts else None
        print(json.dumps(out, indent=2))
        return 0
    print(f"CONFLICTWATCH early-warning  (scope={args.scope}, window={args.window}d, "
          f"baseline={args.baseline_windows}x)")
    print(f"  {len(alerts)} alert(s)   highest={s['highest']}   "
          f"by-severity={s['by_severity']}")
    if not alerts:
        print("  no escalation signals above threshold.")
        return 0
    for a in alerts:
        mark = _SEV_MARK.get(a["severity"], "   ")
        print(f"\n  {mark} [{a['severity']:<8}] {a['detector']:<16} {a['scope']}")
        print(f"        {a['reason']}  (score {a['score']})")
    return 0


def _cmd_sanctions(args) -> int:
    from conflictwatch import sanctions
    events = _load_events(args.input)
    flagged = sanctions.screen_events(events, offline=args.offline)
    if args.format == "json":
        print(json.dumps(flagged, indent=2))
        return 0
    print(f"OFAC SDN screening: {len(flagged)} of {len(events)} events name a sanctioned actor")
    for f in flagged:
        print(f"\n[{f['date']}] {f.get('country','')}  event {f['event_id']}")
        for m in f["matches"]:
            tag = "STRONG" if m["strong"] else f"{m['shared_terms']} shared"
            print(f"  actor '{m['actor']}'  ->  SDN '{m['sdn_name']}'"
                  f"  ({m['sdn_type']}/{m['program']}) [{tag}]")
    return 0


_TIER_MARK = {"red": "!!!", "amber": "!! ", "guarded": "!  ", "green": "   "}


def _cmd_correlate(args) -> int:
    events = _load_events(args.input)
    if args.mode == "all":
        result = correlate_mod.summary(events)
        if args.format == "json":
            print(json.dumps(result, indent=2)); return 0
        _print_clusters(result["clusters"])
        _print_actor_network(result["actor_network"])
        _print_cooccurrence(result["cooccurrence"])
        _print_coordinated(result["coordinated_days"])
        return 0
    if args.mode == "clusters":
        data = correlate_mod.clusters(events, radius_km=args.radius_km,
                                      max_day_gap=args.max_day_gap,
                                      min_size=args.min_size)
        printer = _print_clusters
    elif args.mode == "actor-network":
        data = correlate_mod.actor_network(events)
        printer = _print_actor_network
    elif args.mode == "cooccurrence":
        data = correlate_mod.cooccurrence(events)
        printer = _print_cooccurrence
    else:  # coordinated
        data = correlate_mod.coordinated_days(events)
        printer = _print_coordinated
    if args.format == "json":
        print(json.dumps(data, indent=2))
    else:
        printer(data)
    return 0


def _print_clusters(clusters):
    print(f"{len(clusters)} spatio-temporal cluster(s):")
    for c in clusters:
        lo, hi = c["days"]
        print(f"\n  cluster: {c['size']} events, {c['fatalities']} fatalities "
              f"({lo}..{hi}, {c['span_days']}d span)")
        print(f"    centroid ~({c['centroid']['lat']}, {c['centroid']['lon']}), "
              f"radius {c['radius_km']}km  countries={c['countries']}")
        if c["actors"]:
            print(f"    actors: {', '.join(c['actors'])}")


def _print_actor_network(net):
    print(f"actor network: {len(net['nodes'])} node(s), {len(net['edges'])} edge(s)")
    for e in net["edges"][:15]:
        print(f"  {e['source']} <-> {e['target']}  "
              f"weight={e['weight']} fatalities={e['fatalities']}")


def _print_cooccurrence(pairs):
    print(f"{len(pairs)} recurring event-type co-occurrence(s):")
    for p in pairs:
        print(f"  {p['types'][0]} + {p['types'][1]}  x{p['count']}  "
              f"({', '.join(p['places'][:4])})")


def _print_coordinated(days):
    print(f"{len(days)} coordinated (multi-location) day(s):")
    for d in days:
        print(f"  {d['date']}  {d['locations']} places, {d['events']} events, "
              f"{d['fatalities']} fatalities")


def _cmd_posture(args) -> int:
    events = _load_events(args.input)
    s = indicators_mod.summary(events, scope=args.scope, window=args.window,
                               baseline_windows=args.baseline_windows,
                               as_of=args.as_of)
    if args.format == "json":
        print(json.dumps(s, indent=2)); return 0
    print(f"CONFLICTWATCH I&W posture  (scope={args.scope}, window={args.window}d)")
    print(f"  {s['scopes']} scope(s)   highest={s['highest'].upper()}   "
          f"by-tier={ {k.upper(): v for k, v in s['by_tier'].items()} }")
    for pst in s["postures"]:
        mark = _TIER_MARK.get(pst["tier"], "   ")
        print(f"\n  {mark} [{pst['tier'].upper():<7}] {pst['scope']}  "
              f"(score {pst['score']}, {pst['recent_events']} events / "
              f"{pst['recent_fatalities']} fatalities)")
        for adv in pst["advisories"]:
            print(f"        - {adv}")
    return 0


def _cmd_trends(args) -> int:
    events = _load_events(args.input)
    s = trends_mod.summary(events, metric=args.metric, window=args.window,
                           horizon=args.horizon)
    if args.format == "json":
        print(json.dumps(s, indent=2)); return 0
    print(f"CONFLICTWATCH trends  (metric={s['metric']})")
    fc = s["forecast"]
    print(f"  trend: {fc['direction']} (slope {fc['slope_per_day']}/day over "
          f"{fc['fit_days']}d)")
    print(f"  peaks ({len(s['peaks'])}):")
    for pk in s["peaks"][:8]:
        print(f"    {pk['date']}  value={pk['value']}  z={pk['z']}")
    print(f"  lulls ({len(s['lulls'])}):")
    for lu in s["lulls"][:5]:
        print(f"    {lu['start']}..{lu['end']}  ({lu['days']}d quiet)")
    print("  weekday profile (mean):")
    for wd in s["weekday_profile"]:
        print(f"    {wd['weekday']}  mean={wd['mean']}  total={wd['total']}")
    if fc["projection"]:
        p0, pN = fc["projection"][0], fc["projection"][-1]
        print(f"  forecast next {len(fc['projection'])}d: "
              f"{p0['date']}={p0['value']} .. {pN['date']}={pN['value']}")
    return 0


def _cmd_brief(args) -> int:
    events = _load_events(args.input)
    kwargs = {}
    if args.to in ("markdown", "intsum"):
        kwargs["window"] = args.window
    text = reports_mod.render(events, args.to, **kwargs)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text if text.endswith("\n") else text + "\n")
        print(f"wrote {args.to} brief ({len(events)} events) to {args.output}",
              file=sys.stderr)
    else:
        print(text)
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
