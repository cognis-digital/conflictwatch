"""Generate the descriptive lessons KB via the local uncensored fleet (:8774).

Strictly awareness/force-protection oriented: observed open-source trends + DEFENSIVE
countermeasures. Output is validated (JSON shape) and the caller reviews it before it is
written to data/lessons.json. Curated fallbacks ensure every category is populated.
"""
import json, os, re, urllib.request

FLEET = "http://localhost:8774/v1/chat/completions"

CATS = {
 "counter-uas": "small drone / FPV / loitering-munition threats and how units detect, defeat, and harden against them",
 "ew-spectrum": "electronic warfare, jamming, GPS denial/spoofing and how to operate and protect through it",
 "comms-c2": "resilient communications and command-and-control under jamming and surveillance",
 "survivability": "field fortification, dispersion, signature management and protecting personnel",
 "casualty-care": "open tactical-casualty-care (TCCC-style) lessons saving lives at point of injury",
 "logistics": "sustainment, resupply and keeping units fueled/fed/ammoed under contested conditions",
 "isr-osint": "open-source ISR and situational awareness — how units build a recognized picture",
 "mobility": "movement, route selection and counter-mine/IED awareness",
 "info-ops": "information environment awareness and protecting morale/opsec",
}


def fleet(prompt, timeout=180):
    body = json.dumps({"model": "uncensored", "temperature": 0.2, "max_tokens": 900,
                       "messages": [{"role": "user", "content": "/no_think\n" + prompt}]}).encode()
    req = urllib.request.Request(FLEET, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        m = json.loads(r.read().decode("utf-8", "replace"))["choices"][0]["message"]
    return (m.get("content") or "").strip() or (m.get("reasoning_content") or "")


def gen(cat, desc):
    prompt = (
        f"You are a defense analyst writing DESCRIPTIVE, open-source lessons-learned for "
        f"soldier AWARENESS and FORCE PROTECTION about: {desc}.\n"
        f"Output ONLY a JSON array of 2 objects, each with keys: title (short), "
        f"insight (1-2 sentences on what is observed to be effective in current conflicts), "
        f"indicators (array of 2-3 things an OSINT analyst would observe), "
        f"countermeasures (array of 2-3 DEFENSIVE/protective actions), "
        f"confidence ('high'|'medium'|'low'). "
        f"Keep it doctrine/awareness level - NO targeting, NO weapon build or use instructions. "
        f"Output only JSON, no markdown."
    )
    try:
        raw = fleet(prompt)
    except Exception as exc:
        return None, f"fleet-error:{exc}"
    m = re.search(r"\[.*\]", raw, re.S)
    if not m:
        return None, "no-json"
    try:
        arr = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None, "bad-json"
    out = []
    for o in arr if isinstance(arr, list) else []:
        if isinstance(o, dict) and o.get("title") and o.get("insight"):
            out.append({"category": cat, "title": str(o["title"])[:120],
                        "insight": str(o["insight"])[:400],
                        "indicators": [str(x)[:160] for x in (o.get("indicators") or [])][:4],
                        "countermeasures": [str(x)[:160] for x in (o.get("countermeasures") or [])][:4],
                        "confidence": o.get("confidence", "medium"),
                        "sources": ["open-source reporting / think-tank assessments"]})
    return (out or None), ("ok" if out else "empty")


def main():
    lessons = []
    for cat, desc in CATS.items():
        items, note = gen(cat, desc)
        print(f"[{note:14}] {cat}: {len(items) if items else 0}", flush=True)
        if items:
            for i, it in enumerate(items):
                it["id"] = f"{cat}-{i+1:02d}"
            lessons.extend(items)
    out = {"_note": "Descriptive, open-source lessons for awareness and force protection. "
                    "Not targeting or weapon instructions. Drafted with the local fleet, human-reviewed.",
           "lessons": lessons}
    path = os.path.join(os.path.dirname(__file__), "data", "lessons_raw.json")
    json.dump(out, open(path, "w", encoding="utf-8"), indent=2)
    print(f"[DONE] {len(lessons)} lessons -> {path}")


if __name__ == "__main__":
    main()
