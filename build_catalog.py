"""Merge the per-category source files + curated Brave bookmarks into data/sources.json."""
import json, glob, os, re
from urllib.parse import urlparse

HERE = os.path.dirname(__file__)
entries, seen = [], set()


def add(e):
    u = (e.get("url") or "").strip()
    if not u.startswith("http"):
        return
    key = urlparse(u).netloc.lower().replace("www.", "") + urlparse(u).path.rstrip("/")
    if key in seen:
        return
    seen.add(key)
    e.setdefault("rss", "")
    entries.append({k: e.get(k, "") for k in
                    ("name", "url", "rss", "category", "region", "type", "access", "desc", "tags")})


# 1) agent-gathered category files
for f in sorted(glob.glob(os.path.join(HERE, "_src", "*.json"))):
    try:
        for e in json.load(open(f, encoding="utf-8")):
            add(e)
    except Exception as exc:
        print("skip", f, exc)

# 2) curated Brave bookmarks - PRECISE domain allowlist on netloc (high signal only)
ALLOW = re.compile(
    r"(acled|gdelt|understandingwar|criticalthreats|bellingcat|oryx|liveuamap|deepstatemap|"
    r"janes|rusi|csis|sipri|iiss|rand\.org|cnas|jamestown|crisisgroup|chathamhouse|fpri|"
    r"warontherocks|atlanticcouncil|carnegie|brookings|aspistrategist|lowyinstitute|"
    r"reliefweb|humdata|acaps|fews|unhcr|internal-displacement|icrc|insecurityinsight|"
    r"n2yo|in-the-sky|satview|satellite-tracking|heavens-above|celestrak|space-track|satellitemap|"
    r"flightradar|adsbexchange|adsb|airnavradar|radarbox|opensky|flightaware|"
    r"marinetraffic|vesselfinder|myshiptracking|globalfishingwatch|aishub|aisstream|"
    r"websdr|kiwisdr|rx-tx|sdr\.hu|satdump|"
    r"sentinel|copernicus|dataspace|earthexplorer|usgs|firms|worldview|nasa\.gov/world|"
    r"maxar|vantor|planet\.com|skyfi|mapillary|openstreetmap|overpass|wikimapia|suncalc|peakvisor|"
    r"zoom\.earth|soar|geoconfirmed|"
    r"osintframework|inteltechniques|cybdetective|malfrat|maltego|spiderfoot|shodan|censys|"
    r"securitytrails|hunch\.ly|tracelabs|osintcombine|osintcurio|osintdojo|sherlock|theharvester|"
    r"recon-ng|spiderfoot|ghunt|"
    r"mitre|att&ck|attack\.mitre|airwars|syriahr|snhr|yemendataproject|iraqbodycount|"
    r"longwarjournal|amti|csis|38north|nknews|satp|"
    r"defensenews|breakingdefense|thedrive|twz\.com|defenseone|militarytimes|stripes|navalnews|"
    r"thediplomat|rferl|meduza|kyivindependent|pravda|aljazeera|reuters|apnews|bbc\.co|dw\.com|"
    r"gpsjam|gpswise|dronewars|dronelife|unmannedairspace|militarnyi|spectrum\.ieee)", re.I)
bm = os.path.join(HERE, "_bm_candidates.json")
bm_added = 0
if os.path.exists(bm):
    for c in json.load(open(bm, encoding="utf-8")):
        u, n = c.get("url", ""), c.get("name", "")
        if not ALLOW.search(urlparse(u).netloc):
            continue
        before = len(entries)
        add({"name": n[:80] or urlparse(u).netloc, "url": u, "rss": "", "category": "bookmarks",
             "region": "", "type": "bookmark", "access": "open",
             "desc": (n[:118] or "from curated bookmarks"), "tags": ["bookmark", "osint"]})
        bm_added += len(entries) - before

out = {"_note": "Open conflict/OSINT source catalog for conflictwatch. Public datasets, feeds, "
                "trackers and tools for situational awareness. Respect each provider's terms.",
       "count": len(entries), "sources": entries}
os.makedirs(os.path.join(HERE, "conflictwatch", "data"), exist_ok=True)
json.dump(out, open(os.path.join(HERE, "conflictwatch", "data", "sources.json"), "w", encoding="utf-8"), indent=2)

from collections import Counter
print("TOTAL sources:", len(entries), "| from bookmarks:", bm_added)
print("by category:", dict(Counter(e["category"] for e in entries).most_common()))
print("with RSS:", sum(1 for e in entries if e["rss"]))
