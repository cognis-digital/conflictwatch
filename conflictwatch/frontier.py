"""Build a large harvest frontier from the source catalog.

The catalog holds a few hundred curated conflict/OSINT sources. Many are queryable
per country or per region, so crossing them with a real geographic vocabulary
(ISO-3166 countries) turns the catalog into thousands of concrete, addressable
harvest endpoints for :mod:`conflictwatch.growth`. Awareness/analysis only.
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from .growth import Endpoint, expand

# ISO-3166-1 alpha-2 country codes (the standard 249-code geographic vocabulary).
ISO_3166_ALPHA2: Tuple[str, ...] = (
    "AF", "AX", "AL", "DZ", "AS", "AD", "AO", "AI", "AQ", "AG", "AR", "AM", "AW",
    "AU", "AT", "AZ", "BS", "BH", "BD", "BB", "BY", "BE", "BZ", "BJ", "BM", "BT",
    "BO", "BQ", "BA", "BW", "BV", "BR", "IO", "BN", "BG", "BF", "BI", "CV", "KH",
    "CM", "CA", "KY", "CF", "TD", "CL", "CN", "CX", "CC", "CO", "KM", "CG", "CD",
    "CK", "CR", "CI", "HR", "CU", "CW", "CY", "CZ", "DK", "DJ", "DM", "DO", "EC",
    "EG", "SV", "GQ", "ER", "EE", "SZ", "ET", "FK", "FO", "FJ", "FI", "FR", "GF",
    "PF", "TF", "GA", "GM", "GE", "DE", "GH", "GI", "GR", "GL", "GD", "GP", "GU",
    "GT", "GG", "GN", "GW", "GY", "HT", "HM", "VA", "HN", "HK", "HU", "IS", "IN",
    "ID", "IR", "IQ", "IE", "IM", "IL", "IT", "JM", "JP", "JE", "JO", "KZ", "KE",
    "KI", "KP", "KR", "KW", "KG", "LA", "LV", "LB", "LS", "LR", "LY", "LI", "LT",
    "LU", "MO", "MG", "MW", "MY", "MV", "ML", "MT", "MH", "MQ", "MR", "MU", "YT",
    "MX", "FM", "MD", "MC", "MN", "ME", "MS", "MA", "MZ", "MM", "NA", "NR", "NP",
    "NL", "NC", "NZ", "NI", "NE", "NG", "NU", "NF", "MK", "MP", "NO", "OM", "PK",
    "PW", "PS", "PA", "PG", "PY", "PE", "PH", "PN", "PL", "PT", "PR", "QA", "RE",
    "RO", "RU", "RW", "BL", "SH", "KN", "LC", "MF", "PM", "VC", "WS", "SM", "ST",
    "SA", "SN", "RS", "SC", "SL", "SG", "SX", "SK", "SI", "SB", "SO", "ZA", "GS",
    "SS", "ES", "LK", "SD", "SR", "SJ", "SE", "CH", "SY", "TW", "TJ", "TZ", "TH",
    "TL", "TG", "TK", "TO", "TT", "TN", "TR", "TM", "TC", "TV", "UG", "UA", "AE",
    "GB", "US", "UM", "UY", "UZ", "VU", "VE", "VN", "VG", "VI", "WF", "EH", "YE",
    "ZM", "ZW",
)


def parametric_sources(catalog_sources: Sequence[dict]) -> List[Tuple[str, str]]:
    """Sources that make sense to query per-country (trackers, datasets, tools with URLs)."""
    keep = {"tracker", "dataset", "tool"}
    out: List[Tuple[str, str]] = []
    seen = set()
    for s in catalog_sources:
        url = s.get("url", "")
        name = s.get("name", "")
        if not url.startswith("http") or not name:
            continue
        if s.get("type") in keep and name not in seen:
            seen.add(name)
            out.append((name, url))
    return out


def build_frontier(catalog_sources: Sequence[dict],
                   vocabulary: Optional[Sequence[str]] = None) -> List[Endpoint]:
    """Cross the parametric catalog sources with a geographic vocabulary.

    Returns thousands of concrete endpoints (one per source, plus one per
    source x country), ready to feed :class:`conflictwatch.growth.GrowthEngine`.
    """
    vocab = tuple(vocabulary) if vocabulary is not None else ISO_3166_ALPHA2
    sources = parametric_sources(catalog_sources)
    return expand(sources, vocab, param_name="iso2", base_only=True)
