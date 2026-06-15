# Changelog

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
