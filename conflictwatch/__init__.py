"""conflictwatch - open-source conflict monitoring & situational awareness.

Ingest open conflict datasets (ACLED/GDELT/UCDP) and OSINT feeds into one normalized
event model, analyze hotspots / timelines / actors / escalation trends, and consult a
sourced "what's working" lessons knowledge base for awareness and force protection.

Scope: OSINT and situational awareness only. Descriptive monitoring and defensive
lessons - not targeting, not weapon guidance. See README "Scope & ethics".
"""

TOOL_NAME = "conflictwatch"
TOOL_VERSION = "0.7.0"

from conflictwatch.events import ConflictEvent, dedupe, normalize  # noqa: E402
from conflictwatch import (analyze, catalog, cuas, lessons, scrape, sources,  # noqa: E402
                           intel, datafeeds, sanctions, watch, correlate,
                           indicators, trends, reports, extract, merge,
                           lessonsindex, adapters, entities, dedupstore,
                           advisor, kmlfeed, escalation, actorgraph, actorflux,
                           tempo)
from conflictwatch.intel import to_stix, to_geojson, export  # noqa: E402
from conflictwatch import autonomy  # noqa: E402  (sustainment/CASEVAC ground-autonomy suite)

from .growth import (
    Endpoint, Record, HarvestStore, SourceStats,
    GrowthEngine, GrowthReport, expand, synthetic_fetcher,
)
from .frontier import ISO_3166_ALPHA2, build_frontier, parametric_sources

__all__ = ["ConflictEvent", "normalize", "dedupe", "analyze", "catalog", "cuas", "lessons",
           "scrape", "sources", "intel", "datafeeds", "sanctions", "watch",
           "correlate", "indicators", "trends", "reports", "autonomy",
           "extract", "merge", "lessonsindex", "adapters",
           "entities", "dedupstore", "advisor", "kmlfeed", "escalation", "actorgraph",
           "actorflux", "tempo",
           "to_stix", "to_geojson", "export", "TOOL_NAME", "TOOL_VERSION"]
