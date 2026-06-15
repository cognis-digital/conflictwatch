<a name="top"></a>
# conflictwatch

**Open-source conflict monitoring & situational awareness.** Ingest the standard open
conflict datasets (**ACLED / GDELT / UCDP**) and OSINT feeds into one normalized event
model, analyze **hotspots, timelines, actor activity and escalation trends**, and consult a
sourced **"what's working" lessons** knowledge base for awareness and force protection.

Built for the analyst and the modern soldier who needs a fast, private, recognized picture
from open sources — runs entirely on your own hardware, pure standard library.

Ships with a **111-entry counter-UAS / anti-drone knowledge base** ([COUNTER_UAS.md](COUNTER_UAS.md))
— what's actually working against drones in 2024–2026 (fiber-optic FPVs, acoustic nets,
RF/radar, EW, interceptor drones, counter-Shahed, layered C-UAS, AI autonomy, Western
systems, economics), sourced from open reporting; query it with `conflictwatch cuas`.

Ships with a **catalog of 290+ open conflict/OSINT sources** ([SOURCES.md](SOURCES.md)) —
datasets, trackers, think-tanks, GEOINT/imagery, flight/maritime/SDR tracking, drone &
electronic-warfare monitors, humanitarian/early-warning feeds, and OSINT tooling — plus a
**76-term [GLOSSARY.md](GLOSSARY.md)** of the acronyms, terms and entities of the trade
(OSINT, GEOINT, SIGINT, ISR, EW, C-UAS, FPV, PNT, TCCC, ADS-B, AIS, ACLED, GDELT, UCDP, …).

```mermaid
flowchart LR
  subgraph in["open sources"]
    A[ACLED / UCDP CSV]; G[GDELT 2.0]; R[OSINT RSS/Atom feeds]
  end
  in --> N[[ConflictEvent<br/>normalize + dedup]]
  N --> AN[analyze<br/>hotspots · timeline · actors · trend]
  N --> EM[emit → STIX/MISP/Slack<br/>via cognis-connect]
  L[(lessons KB<br/>what's working)] --> RP[report]
  AN --> RP
  classDef c fill:#6b46c1,color:#fff; class N c;
```

## Scope & ethics

conflictwatch is for **open-source intelligence, situational awareness, and force
protection** — descriptive monitoring of *reported* events and *defensive*
lessons-learned. It is **not** a targeting, fire-control, or weapon system, it does not
plan operations against people, and it is not for surveilling individuals. It reads public
datasets and public feeds only. Use it to understand a situation and protect people.

## Install

```bash
pip install "git+https://github.com/cognis-digital/conflictwatch.git"
```

## Use it

```bash
# 1) Ingest an open dataset (ACLED export shown; also gdelt / ucdp / json)
conflictwatch ingest --source acled --from-file acled_export.csv --out events.json

# 1b) Or pull the latest fully-open GDELT events export (no key)
conflictwatch fetch-gdelt --out events.json

# 1c) Or collect OSINT situational feeds (ISW / ACLED / ReliefWeb / Bellingcat …)
conflictwatch scrape --out osint.json

# Counter-UAS / anti-drone knowledge base (2024-2026 — fiber-optic, acoustic, EW, …)
conflictwatch cuas --topic fiber-optic-drones
conflictwatch cuas --keyword acoustic
conflictwatch cuas --systems            # every named system/program
conflictwatch cuas --stats

# Browse the 290+ source catalog (filter by category/type/access/region/keyword)
conflictwatch sources --stats
conflictwatch sources --category ukraine --access open
conflictwatch sources --has-rss --feeds          # just the RSS URLs (drives `scrape`)

# 2) Situational summary — hotspots, actors, escalation trend
conflictwatch analyze events.json --window 7

# 3) Full report (summary + awareness lessons keyed to what's happening)
conflictwatch report events.json

# 4) "What's working" lessons KB (awareness / force protection)
conflictwatch lessons --category counter-uas
conflictwatch lessons --keyword jamming
```

Example `report` output:

```
CONFLICTWATCH situational summary  (2026-06-10 .. 2026-06-14)
  events: 8   fatalities: 28
  severity: {'high': 2, 'medium': 4, 'info': 2}
  trend (UP): recent 8 vs prior 0 (+100.0%)
  hotspots:
    Borderland   East Province   events=4  fatalities=17
  top actors:
    Forces of A                  events=6  fatalities=27
Relevant lessons (awareness):
  - [counter-uas] Small Drone Threats: low-cost drones used for surveillance and attack — integrate detection + layered defense
```

## From Python

```python
from conflictwatch import sources, analyze, lessons
events = sources.parse("acled", open("acled_export.csv", encoding="utf-8").read())
print(analyze.summary(events)["hotspots"])
print(lessons.query(category="ew-spectrum"))
```

## The "what's working" lessons KB

A sourced, descriptive knowledge base of how modern conflict is actually being fought,
across **counter-UAS, EW/spectrum, comms/C2, survivability, casualty-care, logistics,
ISR/OSINT, mobility, info-ops** — each entry is an observed trend with OSINT **indicators**
and **defensive countermeasures**. Drafted with a local model and human-reviewed; entirely
awareness/protection oriented (`conflictwatch/data/lessons.json`).

## Source catalog (290+) & glossary

[SOURCES.md](SOURCES.md) is the full, queryable catalog (`conflictwatch sources`) across:
**conflict-event datasets** (ACLED, GDELT, UCDP, GTD, SIPRI, PRIO, COW, V-Dem) · **Ukraine**
(ISW, DeepStateMap, Oryx, CIT, Bellingcat) · **MENA** (SOHR, Airwars, Yemen Data Project) ·
**Africa/Sahel** · **Indo-Pacific** (AMTI, 38 North, SCSPI) · **think-tanks** (RUSI, CSIS,
IISS, RAND, War on the Rocks) · **humanitarian/early-warning** (ReliefWeb, HDX, IOM DTM,
ACAPS, FEWS NET, IPC) · **GEOINT** (Copernicus/Sentinel, NASA FIRMS, Maxar, Planet,
GeoConfirmed, SunCalc) · **tracking** (ADS-B Exchange, Flightradar24, MarineTraffic,
CelesTrak, WebSDR) · **drone/EW** (The War Zone, GPSJAM, Drone Wars UK) · **news** wires
with RSS · **OSINT tooling** (OSINT Framework, Bellingcat toolkit, Maltego, SpiderFoot) ·
and curated analyst bookmarks.

88 sources expose RSS — `conflictwatch scrape` pulls straight from the catalog. See
[GLOSSARY.md](GLOSSARY.md) for the 76 acronyms/terms/entities used throughout.

## Integrations & interop

Forward events to STIX/MISP/Sigma/Splunk/Elastic/Slack/webhooks via
[`cognis-connect`](https://github.com/cognis-digital/cognis-connect) —
`conflictwatch ... | python -m conflictwatch.connect --to stix`. See
[INTEGRATIONS.md](INTEGRATIONS.md) and [INTEROP.md](INTEROP.md). Pairs with
[`maritimeint`](https://github.com/cognis-digital/maritimeint),
[`uaslog`](https://github.com/cognis-digital/uaslog), and the drone-OSINT repos.

## License

[COCL 1.0](LICENSE) — © 2026 Cognis Digital LLC.

<div align="right"><a href="#top">↑ back to top</a></div>
