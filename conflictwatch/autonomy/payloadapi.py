"""payloadapi — open-API payload/adapter layer with a conformance harness.

The programme direction is explicit: build to open, documented APIs and avoid proprietary
interfaces, so new sensors, radios, and cargo/CASEVAC modules can be onboarded in *days*.
This module is that adapter framework. A payload advertises a `PayloadSpec` — its kind,
API version, the capabilities it claims, the endpoints it exposes, and the telemetry it
publishes. An `AdapterRegistry` maps payload *kinds* to adapter builders. Before anything
is integrated, a `ConformanceHarness` verifies the payload actually *behaves as advertised*
against the open contract, so a mis-declared or non-conforming module is caught at the door
rather than mid-mission.

Everything here is integration plumbing and validation for logistics/CASEVAC/sensor
payloads. It contains no weapon control, targeting, or engagement logic. Pure stdlib,
deterministic, offline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

# the open contract version this layer implements
CONTRACT_VERSION = "1.0.0"

# recognised payload kinds (open categories; extendable by registering adapters)
KINDS = ("sensor", "radio", "cargo", "casevac", "navigation", "generic")


@dataclass
class PayloadSpec:
    """What a payload advertises about itself over the open API."""
    id: str
    kind: str
    api_version: str                 # semver the payload speaks
    capabilities: list = field(default_factory=list)
    endpoints: list = field(default_factory=list)     # e.g. ["health", "telemetry"]
    telemetry_fields: list = field(default_factory=list)
    vendor: str = ""

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"unknown payload kind {self.kind!r}")
        if not _SEMVER.match(self.api_version):
            raise ValueError(f"api_version {self.api_version!r} is not semver")


_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")

# minimum endpoints every payload must expose to be integrable
REQUIRED_ENDPOINTS = ("health", "describe", "telemetry")
# additional endpoints required per kind
_KIND_ENDPOINTS = {
    "sensor": ("read",),
    "radio": ("send", "receive"),
    "cargo": ("manifest",),
    "casevac": ("vitals",),
    "navigation": ("pose",),
    "generic": (),
}


def semver_compatible(have: str, want: str) -> bool:
    """True if ``have`` satisfies ``want`` under same-major, have-minor >= want-minor."""
    if not (_SEMVER.match(have) and _SEMVER.match(want)):
        raise ValueError("both versions must be semver X.Y.Z")
    h = tuple(int(x) for x in have.split("."))
    w = tuple(int(x) for x in want.split("."))
    if h[0] != w[0]:
        return False
    if h[1] != w[1]:
        return h[1] > w[1]
    return h[2] >= w[2]


class AdapterRegistry:
    """Maps payload kinds to adapter builder callables (open plug-in points)."""

    def __init__(self):
        self._builders: dict[str, Callable] = {}

    def register(self, kind: str, builder: Callable) -> None:
        if kind not in KINDS:
            raise ValueError(f"unknown payload kind {kind!r}")
        self._builders[kind] = builder

    def kinds(self) -> list:
        return sorted(self._builders)

    def build(self, spec: PayloadSpec):
        if spec.kind not in self._builders:
            raise KeyError(f"no adapter registered for kind {spec.kind!r}")
        return self._builders[spec.kind](spec)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


class ConformanceHarness:
    """Verify a payload behaves as advertised before it is integrated.

    A *device* is any object exposing methods named for the endpoints it claims
    (``device.health()``, ``device.describe()``, ...). The harness checks contract
    compatibility, required + per-kind endpoints, that advertised endpoints are actually
    callable, that ``describe()`` matches the spec, and that telemetry carries the declared
    fields. Returns a structured pass/fail report — no exceptions on a failed payload.
    """

    def __init__(self, contract_version: str = CONTRACT_VERSION):
        self.contract_version = contract_version

    def check(self, spec: PayloadSpec, device) -> dict:
        results: list[CheckResult] = []

        # 1. contract compatibility
        try:
            compat = semver_compatible(spec.api_version, self.contract_version)
        except ValueError:
            compat = False
        results.append(CheckResult(
            "contract_version", compat,
            f"payload {spec.api_version} vs contract {self.contract_version}"))

        # 2. required + per-kind endpoints are advertised
        need = list(REQUIRED_ENDPOINTS) + list(_KIND_ENDPOINTS.get(spec.kind, ()))
        missing = [e for e in need if e not in spec.endpoints]
        results.append(CheckResult(
            "endpoints_declared", not missing,
            "missing: " + ", ".join(missing) if missing else "all declared"))

        # 3. advertised endpoints are actually callable on the device
        not_callable = [e for e in spec.endpoints if not callable(getattr(device, e, None))]
        results.append(CheckResult(
            "endpoints_callable", not not_callable,
            "not callable: " + ", ".join(not_callable) if not_callable else "ok"))

        # 4. health responds truthy
        health_ok = False
        if callable(getattr(device, "health", None)):
            try:
                health_ok = bool(device.health())
            except Exception as exc:                     # noqa: BLE001 - report, don't raise
                results.append(CheckResult("health", False, f"raised {exc!r}"))
                health_ok = None
        if health_ok is not None:
            results.append(CheckResult("health", health_ok,
                                       "healthy" if health_ok else "unhealthy"))

        # 5. describe() agrees with the advertised spec
        describe_ok = False
        detail = "no describe()"
        if callable(getattr(device, "describe", None)):
            try:
                d = device.describe() or {}
                describe_ok = (d.get("id") == spec.id and d.get("kind") == spec.kind)
                detail = "matches spec" if describe_ok else f"mismatch: {d}"
            except Exception as exc:                     # noqa: BLE001
                detail = f"raised {exc!r}"
        results.append(CheckResult("describe_matches", describe_ok, detail))

        # 6. telemetry carries the declared fields
        tele_ok = True
        detail = "no telemetry fields declared"
        if spec.telemetry_fields:
            detail = "telemetry endpoint not callable"
            if callable(getattr(device, "telemetry", None)):
                try:
                    sample = device.telemetry() or {}
                    absent = [f for f in spec.telemetry_fields if f not in sample]
                    tele_ok = not absent
                    detail = "all fields present" if tele_ok else "absent: " + ", ".join(absent)
                except Exception as exc:                 # noqa: BLE001
                    tele_ok = False
                    detail = f"raised {exc!r}"
            else:
                tele_ok = False
        results.append(CheckResult("telemetry_fields", tele_ok, detail))

        passed = all(r.ok for r in results)
        return {
            "payload": spec.id,
            "kind": spec.kind,
            "conformant": passed,
            "checks": [{"name": r.name, "ok": r.ok, "detail": r.detail} for r in results],
            "passed": sum(1 for r in results if r.ok),
            "total": len(results),
        }
