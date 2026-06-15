# Data sources

conflictwatch reads **open** conflict data and **public** OSINT feeds. Bulk access to some
datasets requires free registration; respect each provider's terms and rate limits.

## Structured conflict-event datasets

| Source | What | Access | Adapter |
|---|---|---|---|
| **ACLED** — Armed Conflict Location & Event Data | Curated, geocoded political-violence & protest events worldwide | Free registration; CSV/API export | `--source acled` |
| **GDELT 2.0** | Machine-coded global event stream from world news (15-min cadence) | Fully open, no key | `--source gdelt` / `fetch-gdelt` |
| **UCDP GED** — Uppsala Conflict Data Program, Georeferenced Event Dataset | Academic organized-violence deaths dataset | Open download | `--source ucdp` |

## OSINT situational feeds (public RSS/Atom)

| Source | What |
|---|---|
| **ISW** — Institute for the Study of War | Campaign assessments & control-of-terrain analysis |
| **ACLED analysis** | Regional situation reports & curated data analysis |
| **ReliefWeb (OCHA)** | Humanitarian situation reports |
| **Bellingcat** | Open-source investigations & geolocation verification |

Edit the feed list in `conflictwatch/scrape.py` (`DEFAULT_FEEDS`) or pass `--feeds`.

## Lessons knowledge base

`conflictwatch/data/lessons.json` distills **open** reporting (think-tank assessments,
after-action analysis, OSINT) into descriptive, defensive lessons. It carries observed
trends, OSINT indicators, and protective countermeasures — not targeting or weapon
instructions. Drafted with a local model and human-reviewed.

> Heuristic, open-source situational awareness. Corroborate before acting; OSINT can be
> incomplete, delayed, or deliberately manipulated.
