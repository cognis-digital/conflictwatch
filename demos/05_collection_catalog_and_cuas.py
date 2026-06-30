"""Scenario 5 - collection managers & planners: discover sources, brief the threat.

Before monitoring a region you decide *what to watch*. conflictwatch ships a
queryable catalog of 290+ open conflict/OSINT sources and a counter-UAS knowledge
base, so collection planning and a threat brief come straight out of the box -
offline.

This demo:
  * profiles the source catalog (counts, RSS coverage that drives `scrape`),
  * filters it the way a collection manager would (open-access feeds for a topic),
  * and pulls a counter-UAS threat brief (named systems, a topic's entries).

Pure offline, descriptive force-protection awareness only.
"""
from _common import rule

from conflictwatch import catalog, cuas


def main() -> None:
    rule("COLLECTION MANAGER  -  source discovery + counter-UAS threat brief")

    # --- 1) profile the source catalog ------------------------------------ #
    st = catalog.stats()
    print(f"\nSource catalog: {st['total']} sources, {st['with_rss']} expose RSS "
          "(those drive `conflictwatch scrape`).")
    print("  by access  :", st["by_access"])
    top_cats = dict(list(st["by_category"].items())[:6])
    print("  top categories:", top_cats)

    # --- 2) filter like a collection manager: open RSS feeds, keyword ------ #
    feeds = catalog.filter_sources(access="open", has_rss=True, keyword="drone")
    print(f"\nOpen-access RSS sources matching 'drone' -> {len(feeds)}:")
    for s in feeds[:5]:
        print(f"    [{s.get('category',''):<14}] {s.get('name','')}")
        print(f"        {s.get('rss','')}")

    # --- 3) counter-UAS threat brief -------------------------------------- #
    cstats = cuas.stats()
    print(f"\nCounter-UAS KB: {cstats['total']} entries across {len(cstats['by_topic'])} "
          f"topics, {cstats['named_systems']} named systems, "
          f"{cstats['unique_sources']} unique sources.")
    sys_names = cuas.systems()
    print(f"  named systems (sample): {', '.join(sys_names[:10])} ...")

    topic = "ew-jamming"
    entries = cuas.query(topic=topic)
    print(f"\nThreat brief - topic '{topic}' ({len(entries)} entr(ies)):")
    for e in entries[:2]:
        print(f"\n  [{e.get('confidence','?')}] {e['title']}  ({e.get('date','')})")
        if e.get("summary"):
            print(f"      {e['summary'][:160]}")
        if e.get("countermeasures"):
            print("      defense: " + "; ".join(e["countermeasures"][:2])[:160])

    print("\nCollection plan + threat brief, no subscriptions, no network.")


if __name__ == "__main__":
    main()
