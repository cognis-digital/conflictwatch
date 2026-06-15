"""Render SOURCES.md (all catalog sources) + GLOSSARY.md / data/glossary.json."""
import json, os
from collections import Counter

HERE = os.path.dirname(__file__)
SRC = json.load(open(os.path.join(HERE, "conflictwatch", "data", "sources.json"), encoding="utf-8"))
src = SRC["sources"]

CAT_TITLES = {
    "datasets": "Conflict-event datasets & academic data",
    "ukraine": "Ukraine–Russia war OSINT & maps",
    "mena": "Middle East & North Africa monitors",
    "africa": "Sub-Saharan Africa & Sahel monitors",
    "indopacific": "Indo-Pacific & Asia security trackers",
    "thinktank": "Defense & security think-tanks",
    "humanitarian": "Humanitarian, displacement & crisis early-warning",
    "geoint": "GEOINT, satellite imagery & geolocation/verification",
    "tracking": "Flight, maritime, satellite & signal (SDR) tracking",
    "drone-ew": "Drone / UAS / counter-UAS & electronic warfare",
    "news": "News, wire & defense-trade outlets (RSS)",
    "osint-tools": "OSINT frameworks, tools & tradecraft",
    "bookmarks": "Curated analyst bookmarks",
}
ORDER = list(CAT_TITLES)

# ---- SOURCES.md -------------------------------------------------------------
all_tags = Counter(t for s in src for t in (s.get("tags") or []))
lines = ["# Sources catalog", "",
         f"**{len(src)} open conflict / OSINT sources** for situational awareness — public datasets, "
         "feeds, trackers and tools. Generated from `conflictwatch/data/sources.json` "
         "(`conflictwatch sources` to query). Respect each provider's terms and rate limits.", "",
         f"`{sum(1 for s in src if s.get('rss'))}` expose an RSS/Atom feed (drive the scraper with "
         "`conflictwatch scrape`). Access: **open** = no key · **registration** = free account · "
         "**paid** = subscription.", ""]
for cat in ORDER + [c for c in {s["category"] for s in src} if c not in ORDER]:
    items = [s for s in src if s.get("category") == cat]
    if not items:
        continue
    lines.append(f"\n## {CAT_TITLES.get(cat, cat.title())}  ({len(items)})\n")
    lines.append("| Source | Access | RSS | What |")
    lines.append("|---|---|---|---|")
    for s in sorted(items, key=lambda x: x.get("name", "").lower()):
        rss = "✓" if s.get("rss") else ""
        name = s.get("name", "?").replace("|", "\\|")
        desc = (s.get("desc", "") or "").replace("|", "\\|")
        lines.append(f"| [{name}]({s['url']}) | {s.get('access','')} | {rss} | {desc} |")
lines += ["", "## Tags / terms index", "",
          " · ".join(f"`{t}` ({n})" for t, n in all_tags.most_common(60)), "",
          "> Heuristic, open-source situational awareness. Corroborate before acting; OSINT can be "
          "incomplete, delayed, or manipulated. OSINT/force-protection use only."]
open(os.path.join(HERE, "SOURCES.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("wrote SOURCES.md:", len(src), "sources")

# ---- Glossary (terms / acronyms / entities) --------------------------------
GLOSSARY = [
 ("OSINT", "Open-Source Intelligence", "discipline", "Intelligence from publicly available sources."),
 ("GEOINT", "Geospatial Intelligence", "discipline", "Imagery + geospatial analysis of activity on the earth."),
 ("SIGINT", "Signals Intelligence", "discipline", "Intelligence from intercepted signals (COMINT + ELINT)."),
 ("COMINT", "Communications Intelligence", "discipline", "SIGINT from communications content/metadata."),
 ("ELINT", "Electronic Intelligence", "discipline", "SIGINT from non-communication emitters (e.g. radar)."),
 ("HUMINT", "Human Intelligence", "discipline", "Intelligence from human sources."),
 ("IMINT", "Imagery Intelligence", "discipline", "Intelligence from imagery (satellite, aerial, FMV)."),
 ("MASINT", "Measurement and Signature Intelligence", "discipline", "Technical signatures (acoustic, seismic, RF)."),
 ("SOCMINT", "Social Media Intelligence", "discipline", "OSINT from social-media platforms."),
 ("TECHINT", "Technical Intelligence", "discipline", "Intelligence on adversary materiel/capabilities."),
 ("FININT", "Financial Intelligence", "discipline", "Intelligence from financial flows/sanctions data."),
 ("ISR", "Intelligence, Surveillance, Reconnaissance", "C2", "Sensing + collection feeding decisions."),
 ("ISTAR", "Intelligence, Surveillance, Target Acquisition, Reconnaissance", "C2", "ISR plus target acquisition."),
 ("C2", "Command and Control", "C2", "Exercise of authority over assigned forces."),
 ("C4ISR", "Command, Control, Communications, Computers, ISR", "C2", "Integrated C2 + ISR enterprise."),
 ("EW", "Electronic Warfare", "ew", "Military use of the EM spectrum (EA/EP/ES)."),
 ("EA", "Electronic Attack", "ew", "Jamming/disruption of the EM spectrum."),
 ("EP", "Electronic Protection", "ew", "Protecting friendly use of the EM spectrum."),
 ("ES", "Electronic Support", "ew", "Searching/intercepting EM emissions for SA/threat warning."),
 ("EMCON", "Emission Control", "ew", "Disciplined control of emissions to limit detection."),
 ("EMS", "Electromagnetic Spectrum", "ew", "The range of EM frequencies units must contest."),
 ("RF", "Radio Frequency", "ew", "EM emissions used for comms, radar, control links."),
 ("GNSS", "Global Navigation Satellite System", "pnt", "Satellite positioning (GPS, GLONASS, Galileo, BeiDou)."),
 ("GPS", "Global Positioning System", "pnt", "US GNSS constellation; common jam/spoof target."),
 ("PNT", "Positioning, Navigation, Timing", "pnt", "Services degraded by GNSS denial; need alt-nav."),
 ("UAS", "Unmanned Aircraft System", "uas", "Drone + control + comms; the air + ground segments."),
 ("UAV", "Unmanned Aerial Vehicle", "uas", "The aircraft element of a UAS."),
 ("FPV", "First-Person View (drone)", "uas", "Pilot-controlled drone flown via onboard video."),
 ("C-UAS", "Counter-Unmanned Aircraft System", "uas", "Detect/track/defeat hostile drones (defensive)."),
 ("ELRS", "ExpressLRS", "uas", "Open long-range RC control-link protocol common on FPV."),
 ("FLIR", "Forward-Looking Infrared", "sensors", "Thermal imaging sensor for day/night detection."),
 ("SAR", "Synthetic Aperture Radar", "sensors", "Radar imaging through cloud/dark (e.g. Sentinel-1)."),
 ("EO", "Electro-Optical", "sensors", "Visible-band imaging sensors."),
 ("NDVI", "Normalized Difference Vegetation Index", "sensors", "Multispectral index; reveals disturbance/digging."),
 ("ADS-B", "Automatic Dependent Surveillance-Broadcast", "tracking", "Aircraft position broadcast; basis of flight trackers."),
 ("AIS", "Automatic Identification System", "tracking", "Ship position broadcast; basis of vessel trackers."),
 ("TLE", "Two-Line Element set", "tracking", "Orbital data used to predict satellite passes."),
 ("SDR", "Software-Defined Radio", "tracking", "Reconfigurable radio for monitoring RF signals."),
 ("TCCC", "Tactical Combat Casualty Care", "medical", "Battlefield trauma care guidelines."),
 ("MARCH", "Massive hemorrhage, Airway, Respiration, Circulation, Hypothermia", "medical", "TCCC treatment priority sequence."),
 ("CASEVAC", "Casualty Evacuation", "medical", "Evacuation of casualties (non-dedicated assets)."),
 ("MEDEVAC", "Medical Evacuation", "medical", "Evacuation on dedicated, marked medical assets."),
 ("IED", "Improvised Explosive Device", "threat", "Homemade explosive device; major mobility threat."),
 ("UXO", "Unexploded Ordnance", "threat", "Munitions that failed to detonate; hazard to movement."),
 ("EOD", "Explosive Ordnance Disposal", "threat", "Render-safe/disposal of explosive hazards."),
 ("OPSEC", "Operations Security", "security", "Protecting indicators that reveal intentions."),
 ("PERSEC", "Personal Security", "security", "Protecting personal info that enables targeting."),
 ("COMSEC", "Communications Security", "security", "Protecting communications from exploitation."),
 ("ROE", "Rules of Engagement", "doctrine", "Directives on when/how force may be used."),
 ("IHL", "International Humanitarian Law", "doctrine", "Law of armed conflict protecting non-combatants."),
 ("PIR", "Priority Intelligence Requirement", "doctrine", "Commander's prioritized intelligence questions."),
 ("EEFI", "Essential Elements of Friendly Information", "doctrine", "Friendly info to protect from the adversary."),
 ("IPB", "Intelligence Preparation of the Battlefield", "doctrine", "Systematic analysis of terrain/weather/threat."),
 ("COA", "Course of Action", "doctrine", "A candidate plan considered during planning."),
 ("ATT&CK", "Adversarial Tactics, Techniques & Common Knowledge", "cyber", "MITRE knowledge base of adversary behavior."),
 ("IOC", "Indicator of Compromise", "cyber", "Artifact indicating malicious activity."),
 ("TTP", "Tactics, Techniques, Procedures", "cyber", "How an actor operates; behavior over indicators."),
 ("IDP", "Internally Displaced Person", "humanitarian", "Displaced within their own country."),
 ("IPC", "Integrated Food Security Phase Classification", "humanitarian", "Standard for acute food-insecurity/famine phases."),
 ("ACLED", "Armed Conflict Location & Event Data", "entity", "Leading disaggregated conflict-event dataset."),
 ("GDELT", "Global Database of Events, Language & Tone", "entity", "Open machine-coded global event stream."),
 ("UCDP", "Uppsala Conflict Data Program", "entity", "Academic organized-violence dataset (GED)."),
 ("GTD", "Global Terrorism Database", "entity", "START's open database of terrorist attacks."),
 ("SIPRI", "Stockholm International Peace Research Institute", "entity", "Arms transfers & military-spending data."),
 ("PRIO", "Peace Research Institute Oslo", "entity", "Conflict research & battle-deaths data."),
 ("ISW", "Institute for the Study of War", "entity", "Daily campaign assessments & control maps."),
 ("RUSI", "Royal United Services Institute", "entity", "UK defense & security think-tank."),
 ("CSIS", "Center for Strategic & International Studies", "entity", "US think-tank; runs AMTI."),
 ("OCHA", "UN Office for the Coordination of Humanitarian Affairs", "entity", "Runs ReliefWeb and HDX."),
 ("AMTI", "Asia Maritime Transparency Initiative", "entity", "CSIS South China Sea monitoring."),
 ("NATO", "North Atlantic Treaty Organization", "entity", "Collective-defense alliance; doctrine source."),
 ("STS", "Ship-to-Ship transfer", "maritime", "At-sea cargo transfer; sanctions-evasion indicator."),
 ("loitering munition", "Loitering munition", "uas", "One-way attack drone that loiters then strikes."),
 ("counter-battery", "Counter-battery fire", "fires", "Detecting and suppressing enemy indirect fire."),
 ("chronolocation", "Chronolocation", "geoint", "Estimating time from shadows/sun in imagery."),
 ("geolocation", "Geolocation", "geoint", "Determining where imagery was captured."),
]
gl = [{"term": t, "expansion": e, "category": c, "note": n} for (t, e, c, n) in GLOSSARY]
json.dump({"count": len(gl), "terms": gl},
          open(os.path.join(HERE, "conflictwatch", "data", "glossary.json"), "w", encoding="utf-8"), indent=2)
g = ["# Glossary - terms, acronyms & entities", "",
     f"{len(gl)} entries spanning OSINT disciplines, EW/spectrum, UAS/C-UAS, PNT, sensors, tracking, "
     "medical (TCCC), security, doctrine, and the key data **entities** this tool ingests.", ""]
for cat in sorted({x["category"] for x in gl}):
    g.append(f"\n### {cat}\n")
    for x in sorted([y for y in gl if y["category"] == cat], key=lambda y: y["term"]):
        g.append(f"- **{x['term']}** — {x['expansion']}: {x['note']}")
open(os.path.join(HERE, "GLOSSARY.md"), "w", encoding="utf-8").write("\n".join(g) + "\n")
print("wrote GLOSSARY.md + glossary.json:", len(gl), "terms")
