"""casevac — patient ride-quality & condition telemetry monitor.

Moving casualties by autonomous vehicle only helps if the ride does not make injuries
worse. This module enforces a *gentle-transport envelope* — bounds on acceleration, jerk,
lateral load, tilt, and speed appropriate to litter-borne patients over rough terrain —
and continuously scores the ride against it, logging every exceedance so the run can be
justified as having moved casualties "without exacerbating injuries."

It ingests a stream of motion samples (from the platform IMU) and optional vitals samples
(from a monitor payload bridged over the open-API layer), and produces:

  * a per-sample verdict against the ride envelope, with the worst offending axis;
  * a rolling **ride-quality score** (0..1, higher = gentler);
  * exceedance events with severity, for the after-action log;
  * a litter/patient status roll-up for at least two casualty positions.

This is transport-safety monitoring only. It observes and constrains vehicle motion and
relays vitals a payload reports; it performs no medical intervention and makes no clinical
decision. Pure stdlib, deterministic, offline.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence


@dataclass
class RideLimits:
    """The gentle-transport envelope for litter-borne casualties."""
    max_accel: float = 1.2           # m/s^2 longitudinal
    max_lateral: float = 1.2         # m/s^2 lateral
    max_jerk: float = 1.0            # m/s^3
    max_tilt_deg: float = 12.0       # chassis roll/pitch
    max_speed: float = 6.0           # m/s

    def as_dict(self) -> dict:
        return {"max_accel": self.max_accel, "max_lateral": self.max_lateral,
                "max_jerk": self.max_jerk, "max_tilt_deg": self.max_tilt_deg,
                "max_speed": self.max_speed}


@dataclass
class MotionSample:
    t: float                         # seconds
    accel: float = 0.0               # longitudinal m/s^2
    lateral: float = 0.0             # lateral m/s^2
    tilt_deg: float = 0.0
    speed: float = 0.0               # m/s


def _sev(ratio: float) -> str:
    """Map an exceedance ratio (>1 means over limit) to a severity band."""
    if ratio <= 1.0:
        return "ok"
    if ratio <= 1.25:
        return "minor"
    if ratio <= 1.6:
        return "moderate"
    return "severe"


class RideMonitor:
    """Streaming ride-quality monitor. Feed `MotionSample`s in time order."""

    def __init__(self, limits: Optional[RideLimits] = None):
        self.limits = limits or RideLimits()
        self._prev: Optional[MotionSample] = None
        self.samples = 0
        self.exceedances: list[dict] = []
        self._score_acc = 0.0

    def ingest(self, s: MotionSample) -> dict:
        """Score one sample against the envelope; returns its verdict."""
        lim = self.limits
        jerk = 0.0
        if self._prev is not None:
            dt = max(1e-6, s.t - self._prev.t)
            jerk = abs(s.accel - self._prev.accel) / dt
        checks = {
            "accel": (abs(s.accel), lim.max_accel),
            "lateral": (abs(s.lateral), lim.max_lateral),
            "jerk": (jerk, lim.max_jerk),
            "tilt": (abs(s.tilt_deg), lim.max_tilt_deg),
            "speed": (abs(s.speed), lim.max_speed),
        }
        worst_axis, worst_ratio = None, 0.0
        for axis, (val, cap) in checks.items():
            ratio = val / cap if cap > 0 else 0.0
            if ratio > worst_ratio:
                worst_ratio, worst_axis = ratio, axis
        verdict = {
            "t": s.t, "ok": worst_ratio <= 1.0 + 1e-9,
            "worst_axis": worst_axis, "worst_ratio": round(worst_ratio, 3),
            "severity": _sev(worst_ratio), "jerk": round(jerk, 4),
        }
        if not verdict["ok"]:
            self.exceedances.append(verdict)
        # per-sample quality: 1 at/under limit, decaying past it
        self._score_acc += 1.0 / (1.0 + max(0.0, worst_ratio - 1.0) ** 2)
        self.samples += 1
        self._prev = s
        return verdict

    def score(self) -> float:
        """Rolling ride-quality score 0..1 (higher = gentler)."""
        if not self.samples:
            return 1.0
        return round(self._score_acc / self.samples, 4)

    def report(self) -> dict:
        by_sev = {}
        for e in self.exceedances:
            by_sev[e["severity"]] = by_sev.get(e["severity"], 0) + 1
        return {
            "samples": self.samples,
            "ride_quality": self.score(),
            "exceedances": len(self.exceedances),
            "by_severity": by_sev,
            "worst": max(self.exceedances, key=lambda e: e["worst_ratio"], default=None),
            "limits": self.limits.as_dict(),
            "acceptable": all(e["severity"] in ("minor",) for e in self.exceedances),
        }


def evaluate_ride(samples: Iterable[MotionSample],
                  limits: Optional[RideLimits] = None) -> dict:
    """Convenience: run a whole motion trace through a `RideMonitor`."""
    m = RideMonitor(limits)
    for s in samples:
        m.ingest(s)
    return m.report()


# ---------------------------------------------------------------------------
# Litter / patient status roll-up (transport bookkeeping only).
# ---------------------------------------------------------------------------
LITTER_STATES = ("empty", "loaded", "secured", "fault")


@dataclass
class Litter:
    position: str                    # e.g. "upper", "lower"
    state: str = "empty"
    patient_id: str = ""
    vitals: dict = field(default_factory=dict)  # last reported vitals (opaque)

    def __post_init__(self):
        if self.state not in LITTER_STATES:
            raise ValueError(f"unknown litter state {self.state!r}")


def manifest_patients(litters: Sequence[Litter], required: int = 2) -> dict:
    """Roll up litter occupancy; verifies the CSO's 'at least two casualties' capacity."""
    loaded = [l for l in litters if l.state in ("loaded", "secured")]
    secured = [l for l in litters if l.state == "secured"]
    faults = [l for l in litters if l.state == "fault"]
    return {
        "positions": len(litters),
        "loaded": len(loaded),
        "secured": len(secured),
        "faults": len(faults),
        "meets_min_casualties": len(loaded) >= required and not faults,
        "all_loaded_secured": len(secured) == len(loaded) and bool(loaded),
        "patients": [{"position": l.position, "state": l.state,
                      "patient_id": l.patient_id,
                      "has_vitals": bool(l.vitals)} for l in litters],
    }
