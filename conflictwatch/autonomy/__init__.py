"""conflictwatch.autonomy — sustainment / CASEVAC ground-autonomy capability suite.

An additive package of small, deterministic, stdlib-only modules modelling the
capabilities an autonomous *last-tactical-mile* logistics/CASEVAC ground (and survey)
platform needs — framed strictly around the U.S. Army CPE Mission Autonomy sustainment
priority and the April-2026 last-tactical-mile CSO.

Scope discipline mirrors the rest of conflictwatch: these are **logistics, navigation,
detection, comms, and monitoring** models only. Nothing here aims, guides-to, or strikes
a target; there is no weapon, fire-control, or one-way-attack function anywhere in the
package. Every module is offline, deterministic, and dependency-free.

Modules
-------
navfusion       GPS-denied navigation fusion (IMU/VIO/LiDAR-SLAM/terrain-relative)
teleop          teleoperation<->autonomy handoff state machine (safety-gated)
missionprofile  resupply<->CASEVAC modular mission-profile reconfiguration
loadplan        Army classes-of-supply manifest & load/CoG planning
sustainment     platoon/company demand forecasting & resupply triggering
emcon           emission-control low-signature route/behaviour planner
routeplan       exposure-aware (viewshed) contested-route planner
casevac         patient ride-quality & litter/condition telemetry monitor
mesh            BLOS comms multi-bearer selection, mesh relay & store-and-forward
payloadapi      open-API payload adapter layer + conformance harness
shadowgeo       side-scan highlight/shadow geometry feature extractor
sonar           side-scan contact detector + mine-like-object ATR pipeline
telemetry       mission telemetry logging, deterministic replay & after-action
sbom            SBOM + NDAA cyber-compliance onboarding gate
"""

from conflictwatch.autonomy import (navfusion, teleop, missionprofile, loadplan,  # noqa: F401,E402
                                    sustainment, emcon, routeplan, casevac, mesh,
                                    payloadapi, shadowgeo, sonar, telemetry, sbom)

__all__ = ["navfusion", "teleop", "missionprofile", "loadplan", "sustainment",
           "emcon", "routeplan", "casevac", "mesh", "payloadapi", "shadowgeo",
           "sonar", "telemetry", "sbom"]
