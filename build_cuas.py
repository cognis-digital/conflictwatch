"""Merge the parallel counter-UAS research into data/counter_uas.json + COUNTER_UAS.md."""
import json, glob, os
from collections import Counter

HERE = os.path.dirname(__file__)

TOPIC_TITLES = {
    "fiber-optic-drones": "Fiber-optic FPV drones (jam-immune) & countermeasures",
    "acoustic-detection": "Acoustic detection networks",
    "rf-radar-detection": "RF & radar detection",
    "ew-jamming": "Electronic warfare / jamming",
    "interceptor-drones": "Interceptor drones (drone-on-drone)",
    "counter-shahed": "Counter-Shahed / one-way-attack defense",
    "layered-cuas": "Layered C-UAS architecture & doctrine",
    "optical-ai": "Optical/IR detection & AI autonomy",
    "western-cuas": "Western / NATO C-UAS systems",
    "economics-adaptation": "Economics & the adaptation cycle",
}
ORDER = list(TOPIC_TITLES)


def _list(v):
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v if x]
    return [str(v)]


entries = []
for f in sorted(glob.glob(os.path.join(HERE, "_cuas", "*.json"))):
    data = json.load(open(f, encoding="utf-8"))
    rows = data if isinstance(data, list) else data.get("entries", [])
    for e in rows:
        if not isinstance(e, dict) or not e.get("title"):
            continue
        entries.append({
            "topic": e.get("topic", os.path.basename(f)[:-5]),
            "title": str(e.get("title", "")),
            "summary": str(e.get("summary", "")),
            "key_facts": _list(e.get("key_facts")),
            "systems": _list(e.get("systems")),
            "countermeasures": _list(e.get("countermeasures")),
            "date": str(e.get("date", "")),
            "sources": _list(e.get("sources")),
            "confidence": e.get("confidence", "medium"),
        })

out = {
    "_note": "Counter-UAS / anti-drone knowledge base - DESCRIPTIVE OSINT & force-protection "
             "from open 2024-2026 reporting on the Russia-Ukraine war. Detection and DEFENSE "
             "awareness only; not weapon build, guidance, or targeting. Verify before relying.",
    "count": len(entries),
    "topics": dict(Counter(e["topic"] for e in entries).most_common()),
    "entries": entries,
}
os.makedirs(os.path.join(HERE, "conflictwatch", "data"), exist_ok=True)
json.dump(out, open(os.path.join(HERE, "conflictwatch", "data", "counter_uas.json"), "w", encoding="utf-8"), indent=2)
print("wrote counter_uas.json:", len(entries), "entries")

# ---- COUNTER_UAS.md ---------------------------------------------------------
src_count = len({s for e in entries for s in e["sources"]})
L = ["# Counter-UAS knowledge base (2024-2026)", "",
     f"**{len(entries)} sourced entries** on counter-drone / anti-drone — detection, defeat, "
     "doctrine, and the threat trends driving them — distilled from open reporting on the "
     "Russia-Ukraine war. Gathered via parallel OSINT research; every entry carries sources, "
     "a date, and a confidence rating.", "",
     "> **Scope:** descriptive open-source intelligence and **force-protection awareness** — "
     "how drones are detected and defeated, and how the threat is evolving. It is **not** a "
     "weapon-build, drone-guidance, or targeting guide. Corroborate before acting.", "",
     "## Contents", ""]
for t in ORDER:
    n = sum(1 for e in entries if e["topic"] == t)
    if n:
        L.append(f"- [{TOPIC_TITLES[t]}](#{t}) ({n})")
L.append("")
for t in ORDER:
    items = [e for e in entries if e["topic"] == t]
    if not items:
        continue
    L.append(f'\n<a name="{t}"></a>\n## {TOPIC_TITLES[t]}\n')
    for e in items:
        conf = e["confidence"]
        L.append(f"### {e['title']}  ·  _{e['date']}_ · confidence: {conf}\n")
        if e["summary"]:
            L.append(e["summary"] + "\n")
        if e["key_facts"]:
            L.append("**Key facts**")
            L += [f"- {x}" for x in e["key_facts"]]
            L.append("")
        if e["systems"]:
            L.append("**Systems / programs:** " + "; ".join(e["systems"]) + "\n")
        if e["countermeasures"]:
            L.append("**Defensive countermeasures**")
            L += [f"- {x}" for x in e["countermeasures"]]
            L.append("")
        if e["sources"]:
            L.append("**Sources:** " + " · ".join(e["sources"][:8]) + "\n")
L.append("\n> Heuristic OSINT synthesis from open reporting (RUSI, CSIS, ISW, The War Zone, "
         "Forbes/Hambling, Defense News, Kyiv Independent, Atlantic Council, Militarnyi, and "
         "others). Figures vary by source and methodology; treat as directional, not doctrine.")
open(os.path.join(HERE, "COUNTER_UAS.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
print("wrote COUNTER_UAS.md ; unique sources:", src_count)
