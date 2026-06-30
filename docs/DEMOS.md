# Demos

Five runnable scenarios in [`../demos/`](../demos/), each written for a different
audience. Every scenario loads its own committed sample data and runs **fully
offline** — no network, no API keys — so you can run them in any order or on their
own, and they double as smoke tests (each exits 0).

```bash
python demos/run_all.py                              # all five, end to end
python demos/02_force_protection_early_warning.py    # or just one
```

The sample data lives next to the demos: `demos/sample_acled.csv` (an ACLED-shaped
export), `demos/sample_escalation.json` (weeks of baseline plus a late surge), and
`demos/sample_events_sanctions.json` (events naming OFAC-listed actors). The
sanctions scenario serves the OFAC SDN list from the committed offline snapshot in
`tests/fixtures/feeds_cache/`, so it never touches the network.

## 1. OSINT analyst — *the situational snapshot*
**Audience:** OSINT analysts.
Ingest an open ACLED-shaped export and ask `analyze` for the four things at the top
of a brief: hotspots (where activity concentrates), the most-active actors, the
event-type mix, and the window-over-window escalation trend. The descriptive
picture, from open data, on local hardware.

## 2. Force protection — *rank what is changing, not what is biggest*
**Audience:** force protection / early-warning.
The place that was loud yesterday is known; the quiet sector taking its first
shelling is the signal. `watch` runs six deterministic detectors (spike,
sustained-trend, lethality-shift, new-actor, geo-spread, new-hotspot) and ranks the
*delta*. The demo then **replays** the same stream as of an earlier day so you can
see the lead time the early-warning would have bought.

## 3. Journalist / researcher — *export to map + structured record*
**Audience:** journalists and academic researchers.
Turn the events into the formats existing tools already eat: a **GeoJSON** point
layer for Leaflet/Mapbox/QGIS/kepler.gl, and a valid **STIX 2.1** bundle (location +
observed-data + note per event, grouped in a report) for OpenCTI and other TIPs.
Zero dependencies, deterministic ids — re-running yields byte-identical STIX.

## 4. NGO / humanitarian — *who's involved, and how to stay safe*
**Audience:** NGOs and humanitarian staff.
Screen an event stream against the **OFAC SDN** list (primary names *and* `a.k.a.`
aliases — note "Houthis" resolving to the listed "ANSARALLAH"), served from the
committed offline snapshot so it runs air-gapped. Then pull the matching
**protection lessons** ("what's working") for the event types present. Descriptive
and defensive only.

## 5. Collection manager — *source discovery + counter-UAS brief*
**Audience:** collection managers and planners.
Profile the 290+ source catalog, filter it the way you'd plan collection
(open-access RSS feeds matching a topic), then pull a counter-UAS threat brief
(named systems and a topic's entries) from the bundled KB. A collection plan and a
threat brief out of the box, no subscriptions.

---

Each demo prints clear, narrated output and exits 0, so they double as smoke tests
— `tests/` covers the same code paths under `pytest`.
