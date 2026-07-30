"""missionprofile — modular mission-profile reconfiguration engine.

The same vehicle must switch between *resupply* and *casualty-evacuation* roles with
minimal reconfiguration. A profile is a declarative bundle of the things that actually
change between roles: the payload/load plan it expects, the ride-comfort envelope it
must respect, the route-planning constraints it drives under, and the telemetry schema
it publishes. This module holds those profiles, validates them, and computes a concrete
**reconfiguration plan** — the ordered list of steps to go from the profile a vehicle is
in now to the profile it needs next — so an operator can see exactly what has to be
swapped and roughly how long it takes.

Nothing here is weapons- or targeting-related; a profile only reconfigures how a logistics
or casualty-transport platform carries load, rides, plans routes, and reports. Pure stdlib,
deterministic, offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

ROLES = ("resupply", "casevac", "isr_survey", "recovery")


@dataclass
class RideEnvelope:
    """Motion limits the platform must respect while carrying this payload."""
    max_speed: float = 8.0           # m/s
    max_accel: float = 2.5           # m/s^2 longitudinal
    max_lateral: float = 2.5         # m/s^2 lateral
    max_jerk: float = 3.0            # m/s^3
    max_tilt_deg: float = 20.0       # chassis tilt

    def as_dict(self) -> dict:
        return {"max_speed": self.max_speed, "max_accel": self.max_accel,
                "max_lateral": self.max_lateral, "max_jerk": self.max_jerk,
                "max_tilt_deg": self.max_tilt_deg}


@dataclass
class RouteConstraints:
    """Route-planner knobs that differ by role."""
    prefer_smooth: bool = False      # weight ride-quality over speed
    max_grade_pct: float = 30.0
    avoid_water_crossing: bool = False
    exposure_weight: float = 1.0     # how hard to avoid exposed terrain (see routeplan)
    time_weight: float = 1.0         # how hard to minimise transit time

    def as_dict(self) -> dict:
        return {"prefer_smooth": self.prefer_smooth, "max_grade_pct": self.max_grade_pct,
                "avoid_water_crossing": self.avoid_water_crossing,
                "exposure_weight": self.exposure_weight, "time_weight": self.time_weight}


@dataclass
class MissionProfile:
    """A complete, declarative role configuration."""
    role: str
    payload_modules: list = field(default_factory=list)  # module ids to mount
    ride: RideEnvelope = field(default_factory=RideEnvelope)
    route: RouteConstraints = field(default_factory=RouteConstraints)
    telemetry_schema: list = field(default_factory=list)  # published telemetry fields
    reconfig_minutes: float = 10.0   # nominal time to fit this profile from bare

    def __post_init__(self):
        if self.role not in ROLES:
            raise ValueError(f"unknown role {self.role!r}; expected one of {ROLES}")

    def as_dict(self) -> dict:
        return {"role": self.role, "payload_modules": list(self.payload_modules),
                "ride": self.ride.as_dict(), "route": self.route.as_dict(),
                "telemetry_schema": list(self.telemetry_schema),
                "reconfig_minutes": self.reconfig_minutes}


# --- library of standard profiles -------------------------------------------
def resupply_profile() -> MissionProfile:
    return MissionProfile(
        role="resupply",
        payload_modules=["cargo_bed", "tie_down_kit", "manifest_scanner"],
        ride=RideEnvelope(max_speed=10.0, max_accel=3.0, max_lateral=3.0,
                          max_jerk=4.0, max_tilt_deg=25.0),
        route=RouteConstraints(prefer_smooth=False, max_grade_pct=35.0,
                               exposure_weight=1.0, time_weight=1.5),
        telemetry_schema=["position", "speed", "manifest_id", "bay_weights", "fuel"],
        reconfig_minutes=8.0,
    )


def casevac_profile() -> MissionProfile:
    return MissionProfile(
        role="casevac",
        payload_modules=["litter_rack_x2", "vitals_bridge", "shock_isolation"],
        ride=RideEnvelope(max_speed=6.0, max_accel=1.2, max_lateral=1.2,
                          max_jerk=1.0, max_tilt_deg=12.0),
        route=RouteConstraints(prefer_smooth=True, max_grade_pct=18.0,
                               exposure_weight=1.4, time_weight=1.0),
        telemetry_schema=["position", "speed", "ride_g", "litter_status",
                          "patient_vitals", "eta_collection_point"],
        reconfig_minutes=12.0,
    )


def profile_for(role: str) -> MissionProfile:
    """Fetch a standard profile by role name."""
    builders = {"resupply": resupply_profile, "casevac": casevac_profile}
    if role not in builders:
        raise ValueError(f"no standard profile for role {role!r}")
    return builders[role]()


# --- reconfiguration planning ------------------------------------------------
# per-module fit/removal time (minutes); default applies to anything unlisted
_MODULE_MINUTES = {
    "cargo_bed": 4.0, "tie_down_kit": 1.5, "manifest_scanner": 1.0,
    "litter_rack_x2": 5.0, "vitals_bridge": 2.0, "shock_isolation": 4.0,
}
_DEFAULT_MODULE_MIN = 2.0


def reconfigure(current: MissionProfile, target: MissionProfile) -> dict:
    """Compute the ordered steps to move from ``current`` to ``target``.

    Removals first, then fits, then the software reconfigure (ride/route/telemetry
    schema). Returns steps, an estimated total time, and the count of shared modules
    that stay mounted (the whole point of a modular platform — fewer swaps).
    """
    cur = list(current.payload_modules)
    tgt = list(target.payload_modules)
    cur_set, tgt_set = set(cur), set(tgt)
    remove = [m for m in cur if m not in tgt_set]
    add = [m for m in tgt if m not in cur_set]
    keep = [m for m in cur if m in tgt_set]

    steps = []
    minutes = 0.0
    for m in remove:
        t = _MODULE_MINUTES.get(m, _DEFAULT_MODULE_MIN)
        minutes += t
        steps.append({"action": "remove", "module": m, "minutes": t})
    for m in add:
        t = _MODULE_MINUTES.get(m, _DEFAULT_MODULE_MIN)
        minutes += t
        steps.append({"action": "fit", "module": m, "minutes": t})

    sw = []
    if current.ride.as_dict() != target.ride.as_dict():
        sw.append("ride_envelope")
    if current.route.as_dict() != target.route.as_dict():
        sw.append("route_constraints")
    if list(current.telemetry_schema) != list(target.telemetry_schema):
        sw.append("telemetry_schema")
    if sw:
        minutes += 1.0
        steps.append({"action": "load_software_profile", "items": sw, "minutes": 1.0})

    return {
        "from_role": current.role,
        "to_role": target.role,
        "remove": remove,
        "fit": add,
        "keep_mounted": keep,
        "software_changes": sw,
        "steps": steps,
        "estimated_minutes": round(minutes, 2),
        "no_op": not steps,
    }


def validate(profile: MissionProfile) -> list:
    """Return a list of human-readable warnings about a profile (empty = clean)."""
    w = []
    r = profile.ride
    if r.max_accel <= 0 or r.max_speed <= 0:
        w.append("ride envelope has non-positive limits")
    if profile.role == "casevac" and r.max_jerk > 2.0:
        w.append("casevac jerk limit is high for patient comfort (>2.0 m/s^3)")
    if profile.role == "casevac" and not profile.route.prefer_smooth:
        w.append("casevac route should prefer_smooth=True")
    if not profile.payload_modules:
        w.append("profile mounts no payload modules")
    if not profile.telemetry_schema:
        w.append("profile publishes no telemetry")
    return w
