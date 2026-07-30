"""teleop — teleoperation-to-autonomy handoff state machine.

An autonomous sustainment/CASEVAC vehicle is never *just* teleoperated or *just*
autonomous; a crew slides a vehicle up and down an autonomy ladder as the situation and
the comms link allow. This module is the safety-gated mode manager that governs those
transitions so they are explicit, auditable, and fail *safe* rather than fail *open*.

Modes, least to most autonomy::

    E_STOP  <  MANUAL  <  SHARED  <  ASSISTED  <  WAYPOINT  <  LEADER_FOLLOW

Every transition is checked against **explicit criteria** (link quality, localization
quality, operator readiness, obstacle-field state). Requests that fail the gate are
rejected with a stated reason — the vehicle never silently assumes more autonomy than
the current conditions justify. Two safety behaviours are always live:

  * **operator override latches** — a human E-stop or take-manual demand is honoured
    immediately from any mode and *latches* until explicitly cleared;
  * **comms-loss fallback** — if the control link degrades past threshold while the
    vehicle is autonomous, it executes the configured lost-link action
    (``hold`` / ``stop`` / ``return_to_last_known``) rather than driving on blind.

This governs the vehicle's *own* driving autonomy for logistics and casualty movement.
It contains no weapon, targeting, or engagement logic of any kind. Pure stdlib,
deterministic, offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# autonomy ladder, ascending
MODES = ("e_stop", "manual", "shared", "assisted", "waypoint", "leader_follow")
_RANK = {m: i for i, m in enumerate(MODES)}

# actions taken when the control link is lost while autonomous
LOSTLINK_ACTIONS = ("hold", "stop", "return_to_last_known")


@dataclass
class Conditions:
    """A snapshot of the inputs the gate reasons over."""
    link_quality: float = 1.0        # 0..1 control-link health
    nav_quality: float = 1.0         # 0..1 localization confidence (see navfusion.quality)
    operator_ready: bool = True      # operator attentive / hands-on-ready
    obstacle_density: float = 0.0    # 0..1 local obstacle field
    speed: float = 0.0               # m/s, current

    def clamp(self) -> "Conditions":
        self.link_quality = _c01(self.link_quality)
        self.nav_quality = _c01(self.nav_quality)
        self.obstacle_density = _c01(self.obstacle_density)
        self.speed = max(0.0, float(self.speed))
        return self


def _c01(x: float) -> float:
    x = float(x)
    return 0.0 if x < 0 else 1.0 if x > 1 else x


# minimum conditions to *enter* each mode (link, nav) and whether operator must be ready
_ENTRY = {
    "manual":        {"link": 0.15, "nav": 0.0,  "op": True},
    "shared":        {"link": 0.35, "nav": 0.25, "op": True},
    "assisted":      {"link": 0.35, "nav": 0.40, "op": True},
    "waypoint":      {"link": 0.20, "nav": 0.60, "op": False},
    "leader_follow": {"link": 0.30, "nav": 0.55, "op": False},
}

# if link/nav falls below these while *in* the mode, the machine self-demotes
_SUSTAIN = {
    "manual":        {"link": 0.10, "nav": 0.0},
    "shared":        {"link": 0.25, "nav": 0.15},
    "assisted":      {"link": 0.25, "nav": 0.30},
    "waypoint":      {"link": 0.15, "nav": 0.45},
    "leader_follow": {"link": 0.20, "nav": 0.40},
}


@dataclass
class Transition:
    ok: bool
    frm: str
    to: str
    reason: str


class ModeManager:
    """The mode state machine. Feed it `request()` demands and `tick()` conditions."""

    def __init__(self, mode: str = "manual",
                 lost_link_action: str = "hold",
                 link_loss_threshold: float = 0.2):
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}")
        if lost_link_action not in LOSTLINK_ACTIONS:
            raise ValueError(f"unknown lost-link action {lost_link_action!r}")
        self.mode = mode
        self.lost_link_action = lost_link_action
        self.link_loss_threshold = float(link_loss_threshold)
        self.override_latched = False
        self.lostlink_active = False
        self.history: list[Transition] = []

    # -- operator override (latching, highest priority) ----------------------
    def estop(self, reason: str = "operator E-stop") -> Transition:
        """Immediate, latching stop from any mode."""
        return self._force("e_stop", reason, latch=True)

    def take_manual(self, reason: str = "operator take-manual") -> Transition:
        """Latching demotion to MANUAL — the classic 'I have control' takeover."""
        return self._force("manual", reason, latch=True)

    def clear_override(self) -> None:
        """Release a latched override so autonomy requests are honoured again."""
        self.override_latched = False

    def _force(self, to: str, reason: str, latch: bool) -> Transition:
        t = Transition(True, self.mode, to, reason)
        self.mode = to
        if latch:
            self.override_latched = True
        self.lostlink_active = False
        self.history.append(t)
        return t

    # -- graded autonomy requests (gated) ------------------------------------
    def request(self, to: str, cond: Optional[Conditions] = None) -> Transition:
        """Request a mode change; allowed only if the entry gate passes."""
        if to not in MODES:
            raise ValueError(f"unknown mode {to!r}")
        cond = (cond or Conditions()).clamp()
        if self.override_latched and _RANK[to] > _RANK["manual"]:
            t = Transition(False, self.mode, to,
                           "override latched — clear_override() before re-engaging autonomy")
            self.history.append(t)
            return t
        if to in ("e_stop", "manual"):
            return self._force(to, f"requested {to}", latch=False)
        gate = _ENTRY[to]
        if gate["op"] and not cond.operator_ready:
            return self._reject(to, "operator not ready")
        if cond.link_quality < gate["link"]:
            return self._reject(to, f"link {cond.link_quality:.2f} < {gate['link']:.2f} required")
        if cond.nav_quality < gate["nav"]:
            return self._reject(to, f"nav {cond.nav_quality:.2f} < {gate['nav']:.2f} required")
        return self._force(to, f"entered {to}", latch=False)

    def _reject(self, to: str, why: str) -> Transition:
        t = Transition(False, self.mode, to, why)
        self.history.append(t)
        return t

    # -- periodic health check (self-demotion + lost-link) -------------------
    def tick(self, cond: Conditions) -> Transition:
        """Re-evaluate the current mode against live conditions.

        Returns a no-op transition if nothing changed. Triggers comms-loss fallback
        when the link drops below threshold in an autonomous mode, and self-demotes to
        MANUAL if a mode's sustain thresholds are no longer met.
        """
        cond = cond.clamp()
        autonomous = _RANK[self.mode] >= _RANK["assisted"]
        if autonomous and cond.link_quality < self.link_loss_threshold:
            return self._lostlink(cond)
        self.lostlink_active = False
        keep = _SUSTAIN.get(self.mode)
        if keep and (cond.link_quality < keep["link"] or cond.nav_quality < keep["nav"]):
            reason = (f"self-demote: link {cond.link_quality:.2f}/nav {cond.nav_quality:.2f} "
                      f"below sustain for {self.mode}")
            return self._force("manual", reason, latch=False)
        return Transition(True, self.mode, self.mode, "nominal")

    def _lostlink(self, cond: Conditions) -> Transition:
        self.lostlink_active = True
        action = self.lost_link_action
        target = "e_stop" if action == "stop" else self.mode
        reason = f"comms-loss fallback: link {cond.link_quality:.2f} -> {action}"
        if action == "stop":
            t = self._force("e_stop", reason, latch=False)
            self.lostlink_active = True
            return t
        # hold / return_to_last_known keep autonomy but change behaviour, not mode
        t = Transition(True, self.mode, self.mode, reason)
        self.history.append(t)
        return t

    # -- introspection --------------------------------------------------------
    def commanded_behavior(self) -> str:
        """What the drive layer should be doing right now."""
        if self.mode == "e_stop":
            return "full_stop"
        if self.lostlink_active:
            return self.lost_link_action
        return {"manual": "teleop", "shared": "shared_control",
                "assisted": "assisted_autonomy", "waypoint": "waypoint_autonomy",
                "leader_follow": "leader_follow"}[self.mode]

    def snapshot(self) -> dict:
        return {"mode": self.mode, "behavior": self.commanded_behavior(),
                "override_latched": self.override_latched,
                "lostlink_active": self.lostlink_active,
                "lost_link_action": self.lost_link_action,
                "transitions": len(self.history)}
