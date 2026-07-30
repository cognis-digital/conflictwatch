"""sbom — software bill-of-materials + NDAA cyber-compliance onboarding gate.

The Army marketplace requires every system to comply with NDAA supply-chain / cybersecurity
rules before use. This module is the CI/onboarding gate that produces a minimal Software
Bill of Materials for a payload or dependency set and checks it against configurable rules:
prohibited-source components (NDAA Section 889-style covered origins), unpinned versions,
disallowed licences, and known-bad component identifiers. A payload that trips a *blocking*
rule is rejected before integration; advisory findings are surfaced but do not block.

This is compliance tooling — it inspects software metadata and emits a pass/fail report.
It has no runtime, control, or targeting function. Pure stdlib, deterministic, offline.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Iterable, Optional

# origins treated as covered/prohibited under NDAA supply-chain rules (illustrative)
PROHIBITED_ORIGINS = ("covered-foreign-entity", "prohibited")
# licences not permitted for integrated components (illustrative policy)
DISALLOWED_LICENSES = ("unknown", "proprietary-nointegration", "agpl-3.0")


@dataclass
class Component:
    name: str
    version: str = ""
    origin: str = "unknown"
    license: str = "unknown"
    supplier: str = ""

    def purl(self) -> str:
        """A package-URL-like identifier for the component."""
        v = self.version or "unpinned"
        return f"pkg:generic/{self.name}@{v}"


@dataclass
class Finding:
    rule: str
    component: str
    blocking: bool
    detail: str


def build_sbom(components: Iterable[Component], name: str = "payload") -> dict:
    """Produce a minimal, deterministic SBOM document for a component set."""
    comps = list(components)
    entries = [{"name": c.name, "version": c.version or "unpinned",
                "origin": c.origin, "license": c.license,
                "supplier": c.supplier, "purl": c.purl()} for c in comps]
    entries.sort(key=lambda e: (e["name"], e["version"]))
    digest = hashlib.sha256(
        json.dumps(entries, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return {"document": name, "component_count": len(entries),
            "components": entries, "digest": digest}


def check_compliance(components: Iterable[Component], *,
                     prohibited_origins: Optional[Iterable[str]] = None,
                     disallowed_licenses: Optional[Iterable[str]] = None,
                     denylist: Optional[Iterable[str]] = None,
                     require_pinned: bool = True) -> dict:
    """Evaluate a component set against NDAA-style rules.

    Returns a report with findings and a ``compliant`` flag (True only when no *blocking*
    finding is present). Blocking: prohibited origin, disallowed licence, deny-listed name.
    Advisory: unpinned version (blocks only when ``require_pinned`` and no worse finding).
    """
    prohibited = set(x.lower() for x in (prohibited_origins or PROHIBITED_ORIGINS))
    lic_block = set(x.lower() for x in (disallowed_licenses or DISALLOWED_LICENSES))
    deny = set(x.lower() for x in (denylist or ()))

    findings: list[Finding] = []
    for c in components:
        if c.origin.lower() in prohibited:
            findings.append(Finding("prohibited_origin", c.name, True,
                                    f"origin {c.origin!r} is covered/prohibited"))
        if c.license.lower() in lic_block:
            findings.append(Finding("disallowed_license", c.name, True,
                                    f"license {c.license!r} not permitted"))
        if c.name.lower() in deny:
            findings.append(Finding("denylisted", c.name, True,
                                    "component is on the deny list"))
        if not c.version:
            findings.append(Finding("unpinned_version", c.name, bool(require_pinned),
                                    "component version is not pinned"))

    blocking = [f for f in findings if f.blocking]
    return {
        "compliant": not blocking,
        "blocking": len(blocking),
        "advisory": len(findings) - len(blocking),
        "findings": [{"rule": f.rule, "component": f.component,
                      "blocking": f.blocking, "detail": f.detail} for f in findings],
        "checked": True,
    }


def gate(components: Iterable[Component], name: str = "payload", **rules) -> dict:
    """One-call onboarding gate: build the SBOM and run compliance in one report."""
    comps = list(components)
    sbom = build_sbom(comps, name=name)
    compliance = check_compliance(comps, **rules)
    return {"sbom": sbom, "compliance": compliance,
            "admit": compliance["compliant"]}
