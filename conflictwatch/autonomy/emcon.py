"""emcon — emission-control (EMCON) low-signature route & behaviour planner.

A resupply/CASEVAC vehicle survives the last mile by being *hard to detect*, especially on
final approach to a supported unit that must not be given away. This module scores a
route's detectable signature across four domains and plans behaviours that shrink it:

  * **RF**       — radio transmit duty cycle (the emitter you most control);
  * **acoustic**  — engine/track noise, dominated by speed, cut by terrain masking;
  * **thermal**  — engine heat load, rising with speed and sustained effort;
  * **visual**   — dust/movement signature, cut by concealment / terrain masking.

Each is a bounded 0..1 detectability. The planner throttles radio duty, selects a slow /
quiet speed profile, favours terrain-masked segments, and enforces a **radio-silent, slow
final-approach** segment to the supported unit — trading time for signature within a stated
time budget. This is detection-avoidance for the *vehicle itself*: reducing how observable
the logistics platform is. It is not a weapon, not a jammer tasker, and not a targeting
aid. Pure stdlib, deterministic, offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

DOMAINS = ("rf", "acoustic", "thermal", "visual")

# speed presets (m/s) the planner may select, quietest first
SPEED_PRESETS = (1.5, 3.0, 5.0, 8.0)


@dataclass
class Segment:
    """One leg of a route with its EMCON-relevant properties."""
    distance_m: float
    speed: float = 3.0               # m/s planned ground speed
    radio_duty: float = 0.1          # 0..1 fraction of time transmitting
    terrain_mask: float = 0.0        # 0..1 concealment from likely observers
    final_approach: bool = False

    def __post_init__(self):
        self.distance_m = max(0.0, float(self.distance_m))
        self.speed = max(0.1, float(self.speed))
        self.radio_duty = _c01(self.radio_duty)
        self.terrain_mask = _c01(self.terrain_mask)

    @property
    def duration_s(self) -> float:
        return self.distance_m / self.speed


def _c01(x: float) -> float:
    x = float(x)
    return 0.0 if x < 0 else 1.0 if x > 1 else x


@dataclass
class SignatureModel:
    """Weights mapping segment properties to per-domain detectability."""
    # speed at which acoustic/visual saturate (m/s)
    speed_saturate: float = 8.0
    # composite weighting across domains (need not sum to 1)
    weights: dict = field(default_factory=lambda: {
        "rf": 0.35, "acoustic": 0.30, "thermal": 0.15, "visual": 0.20})

    def signature(self, seg: Segment) -> dict:
        """Per-domain and composite detectability for a segment (each 0..1)."""
        sp = min(seg.speed / self.speed_saturate, 1.0)
        mask = seg.terrain_mask
        rf = seg.radio_duty
        acoustic = _c01(sp * (1.0 - 0.6 * mask))
        thermal = _c01((0.3 + 0.7 * sp) * (1.0 - 0.3 * mask))
        visual = _c01(sp * (1.0 - 0.8 * mask))
        dom = {"rf": round(rf, 4), "acoustic": round(acoustic, 4),
               "thermal": round(thermal, 4), "visual": round(visual, 4)}
        w = self.weights
        tot = sum(w[k] for k in DOMAINS) or 1.0
        composite = sum(dom[k] * w[k] for k in DOMAINS) / tot
        dom["composite"] = round(composite, 4)
        return dom


def route_signature(segments: Sequence[Segment],
                    model: Optional[SignatureModel] = None) -> dict:
    """Roll up a route's signature: time-weighted composite + per-domain peaks."""
    model = model or SignatureModel()
    segs = list(segments)
    if not segs:
        return {"composite": 0.0, "duration_s": 0.0, "distance_m": 0.0,
                "peak": {d: 0.0 for d in DOMAINS}, "segments": []}
    total_t = sum(s.duration_s for s in segs) or 1.0
    peak = {d: 0.0 for d in DOMAINS}
    acc = 0.0
    per = []
    for s in segs:
        sig = model.signature(s)
        acc += sig["composite"] * s.duration_s
        for d in DOMAINS:
            peak[d] = max(peak[d], sig[d])
        per.append({"distance_m": s.distance_m, "speed": s.speed,
                    "duration_s": round(s.duration_s, 2),
                    "final_approach": s.final_approach, **sig})
    return {
        "composite": round(acc / total_t, 4),
        "duration_s": round(sum(s.duration_s for s in segs), 2),
        "distance_m": round(sum(s.distance_m for s in segs), 2),
        "peak": {d: round(peak[d], 4) for d in DOMAINS},
        "segments": per,
    }


def plan_emcon(segments: Sequence[Segment], time_budget_s: Optional[float] = None,
               final_approach_speed: float = 1.5,
               final_approach_radio: float = 0.0,
               model: Optional[SignatureModel] = None) -> dict:
    """Choose per-segment speed & radio behaviour to minimise signature.

    Strategy: start every segment at its quietest feasible speed and cut radio duty on
    exposed (low terrain-mask) legs; if a ``time_budget_s`` is given and the all-quiet
    plan overruns it, greedily speed up the segments with the best *time-saved-per-unit-
    signature-added* until the budget is met. The final-approach segment is always forced
    slow and radio-silent regardless of budget — the CSO's non-negotiable.
    """
    model = model or SignatureModel()
    segs = [Segment(s.distance_m, s.speed, s.radio_duty, s.terrain_mask, s.final_approach)
            for s in segments]
    # baseline: quietest speed everywhere; radio duty scaled down where exposed
    for s in segs:
        if s.final_approach:
            s.speed = min(final_approach_speed, s.speed)
            s.radio_duty = _c01(final_approach_radio)
        else:
            s.speed = SPEED_PRESETS[0]
            # exposed legs (low mask) go quieter on the radio
            s.radio_duty = _c01(s.radio_duty * (0.2 + 0.8 * s.terrain_mask))

    def total_time():
        return sum(s.duration_s for s in segs)

    changes = []
    if time_budget_s is not None and total_time() > time_budget_s:
        # candidate speed-ups exclude the final-approach leg
        # keep bumping the segment that buys the most time per signature added
        for _ in range(len(segs) * len(SPEED_PRESETS)):
            if total_time() <= time_budget_s:
                break
            best = None
            for idx, s in enumerate(segs):
                if s.final_approach:
                    continue
                cur_i = _preset_index(s.speed)
                if cur_i >= len(SPEED_PRESETS) - 1:
                    continue
                nxt = SPEED_PRESETS[cur_i + 1]
                t_before = s.duration_s
                sig_before = model.signature(s)["composite"]
                probe = Segment(s.distance_m, nxt, s.radio_duty, s.terrain_mask)
                t_after = probe.duration_s
                sig_after = model.signature(probe)["composite"]
                time_saved = t_before - t_after
                sig_added = max(1e-6, sig_after - sig_before)
                ratio = time_saved / sig_added
                if best is None or ratio > best[0]:
                    best = (ratio, idx, nxt)
            if best is None:
                break                 # cannot go faster; budget infeasible at low signature
            _, idx, nxt = best
            segs[idx].speed = nxt
            changes.append({"segment": idx, "new_speed": nxt})

    rollup = route_signature(segs, model)
    rollup["plan"] = [{"distance_m": s.distance_m, "speed": s.speed,
                       "radio_duty": s.radio_duty,
                       "terrain_mask": s.terrain_mask,
                       "final_approach": s.final_approach} for s in segs]
    rollup["time_budget_s"] = time_budget_s
    rollup["within_budget"] = (time_budget_s is None or
                               rollup["duration_s"] <= time_budget_s + 1e-6)
    rollup["speedups"] = changes
    # explicit final-approach assurance for auditability
    fa = [s for s in segs if s.final_approach]
    rollup["final_approach_silent"] = all(s.radio_duty == 0.0 for s in fa) if fa else None
    return rollup


def _preset_index(speed: float) -> int:
    best_i, best_d = 0, None
    for i, p in enumerate(SPEED_PRESETS):
        d = abs(p - speed)
        if best_d is None or d < best_d:
            best_i, best_d = i, d
    return best_i
