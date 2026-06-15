# Changelog

## [0.3.0] — 2026-06-15

The "counter-UAS" release — a large, sourced anti-drone knowledge base.

### Added
- **Counter-UAS / anti-drone knowledge base** (`conflictwatch/data/counter_uas.json` +
  `cuas.py` + [COUNTER_UAS.md](COUNTER_UAS.md)): **111 entries across 10 topics** —
  fiber-optic FPV drones, acoustic detection (Sky Fortress/Zvook), RF & radar, electronic
  warfare/jamming, interceptor drones, counter-Shahed, layered C-UAS doctrine, optical/IR &
  AI autonomy, Western/NATO systems, and economics/adaptation. Each entry has key facts,
  named systems, **defensive** countermeasures, a date, a confidence rating, and sources
  (**335 unique sources** from RUSI/CSIS/ISW/The War Zone/Forbes/Defense News/Atlantic
  Council/Militarnyi/…). Gathered via large parallel OSINT research on the Russia-Ukraine
  war, 2024-2026.
- **`conflictwatch cuas`** CLI — filter by `--topic`/`--keyword`/`--confidence`, list every
  named `--systems`, or print `--stats`.
- Tests (8) incl. a **scope guard** asserting the KB is detection/defense awareness only —
  no build/guidance/targeting content (31 total).

## [0.2.0] — 2026-06-15

The "source catalog" release — a big, curated, queryable source base and a glossary.

### Added
- **Source catalog of 296 open conflict/OSINT sources** (`conflictwatch/data/sources.json`
  + `catalog.py`): conflict-event datasets, Ukraine/MENA/Africa/Indo-Pacific monitors,
  defense think-tanks, humanitarian & early-warning feeds, GEOINT/imagery & geolocation
  tools, flight/maritime/satellite/SDR tracking, drone & electronic-warfare monitors, news
  wires (RSS), OSINT frameworks/tooling, and curated analyst bookmarks. 88 expose RSS.
  Gathered via large parallel web research + filtered from the analyst's own bookmarks.
- **`conflictwatch sources`** CLI — filter by category/type/access/region/keyword, list
  RSS feeds (`--feeds`), or print `--stats`. `scrape` can now pull straight from the
  catalog (`collect_from_catalog`).
- **[SOURCES.md](SOURCES.md)** — the full catalog rendered + a tag/term index.
- **Glossary** (`conflictwatch/data/glossary.json` + [GLOSSARY.md](GLOSSARY.md)): 76
  acronyms/terms/entities across OSINT disciplines, EW, UAS/C-UAS, PNT, sensors, tracking,
  TCCC, security, doctrine, and the key data entities ingested.
- `examples/` — ingest+report, browse-catalog, emit-to-platforms use cases.
- Tests expanded to 23 (catalog size/shape/anchors, glossary, catalog-driven scrape, CLI).

## [0.1.0] — 2026-06-15

Initial release — open-source conflict monitoring & situational awareness.

### Added
- **`ConflictEvent`** normalized model (ACLED-aligned) with field aliasing, type coercion,
  ISO-date parsing, fatality-based severity tiers, and stable dedup ids.
- **Dataset adapters** (`sources.py`): ACLED CSV, GDELT 2.0 TSV (tolerant), UCDP GED CSV,
  generic JSON; plus `fetch-gdelt` for the fully-open latest GDELT export.
- **OSINT scraping** (`scrape.py`): stdlib RSS/Atom collection from public situational
  feeds (ISW / ACLED / ReliefWeb / Bellingcat) → events; polite + offline-testable.
- **Analysis** (`analyze.py`): hotspots, timeline, actor activity, type breakdown, and a
  recent-vs-prior **escalation trend**; one-call `summary()`.
- **Lessons KB** (`lessons.py` + `data/lessons.json`): a sourced, descriptive "what's
  working" knowledge base across 9 categories (counter-UAS, EW/spectrum, comms/C2,
  survivability, casualty-care, logistics, ISR/OSINT, mobility, info-ops) — observed
  trends + OSINT indicators + **defensive** countermeasures. Drafted with a local model,
  human-reviewed; awareness/force-protection only.
- **CLI**: `ingest`, `fetch-gdelt`, `scrape`, `analyze`, `lessons`, `report`.
- **cognis-connect** bridge: `conflictwatch-emit` → STIX/MISP/Sigma/Splunk/Elastic/Slack/
  webhook/brief.
- Cross-OS CI (Linux/macOS/Windows × Py 3.10–3.13); 14 tests including a scope guard that
  asserts the lessons KB carries no targeting/weapon-build content.
