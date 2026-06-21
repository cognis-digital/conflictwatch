"""conflictwatch - open-source conflict monitoring & situational awareness.

Ingest open conflict datasets (ACLED/GDELT/UCDP) and OSINT feeds into one normalized
event model, analyze hotspots / timelines / actors / escalation trends, and consult a
sourced "what's working" lessons knowledge base for awareness and force protection.

Scope: OSINT and situational awareness only. Descriptive monitoring and defensive
lessons - not targeting, not weapon guidance. See README "Scope & ethics".
"""

TOOL_NAME = "conflictwatch"
TOOL_VERSION = "0.4.0"

from conflictwatch.events import ConflictEvent, dedupe, normalize  # noqa: E402
from conflictwatch import analyze, catalog, cuas, lessons, scrape, sources, intel  # noqa: E402
from conflictwatch.intel import to_stix, to_geojson, export  # noqa: E402

__all__ = ["ConflictEvent", "normalize", "dedupe", "analyze", "catalog", "cuas", "lessons",
           "scrape", "sources", "intel", "to_stix", "to_geojson", "export",
           "TOOL_NAME", "TOOL_VERSION"]
