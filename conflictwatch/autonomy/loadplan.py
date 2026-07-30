"""loadplan — classes-of-supply manifest & load-planning engine.

For resupply the vehicle has to haul *enough of the right things* to sustain a dismounted
rifle platoon plus a company headquarters — and it has to do so inside hard payload,
volume, and centre-of-gravity limits. This module is the cargo accountant: it models the
Army classes of supply, packs requested items against the vehicle envelope, computes the
resulting centre of gravity, and emits a digital manifest.

Classes of supply modelled (cargo accounting only)::

    I    subsistence (rations, water is tracked separately)
    II   clothing / individual equipment
    III  petroleum, oils, lubricants (fuel/POL)
    IV   construction / barrier material
    V    ammunition — *carried as inventoried cargo*, quantities only
    VI   personal-demand items
    VII  major end items
    VIII medical materiel
    IX   repair parts
    X    non-military / civil-affairs material
    WATER potable water (tracked explicitly for planning)

Class V appears only as a manifest line — a count and a weight to be transported. This
module does no ballistics, no targeting, and no employment planning; it is inventory and
mass-properties bookkeeping so a logistics run is feasible and documented. Pure stdlib,
deterministic, offline.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional

CLASSES = {
    "I": "subsistence",
    "II": "clothing/individual equipment",
    "III": "POL (fuel/lubricants)",
    "IV": "construction/barrier",
    "V": "ammunition (cargo)",
    "VI": "personal demand",
    "VII": "major end items",
    "VIII": "medical materiel",
    "IX": "repair parts",
    "X": "civil/non-military",
    "WATER": "potable water",
}


@dataclass
class Item:
    """One cargo line item."""
    name: str
    supply_class: str
    unit_weight: float               # kg per unit
    unit_volume: float = 0.0         # m^3 per unit
    quantity: int = 1

    def __post_init__(self):
        self.supply_class = str(self.supply_class).upper()
        if self.supply_class not in CLASSES:
            raise ValueError(f"unknown supply class {self.supply_class!r}")
        self.unit_weight = max(0.0, float(self.unit_weight))
        self.unit_volume = max(0.0, float(self.unit_volume))
        self.quantity = max(0, int(self.quantity))

    @property
    def weight(self) -> float:
        return self.unit_weight * self.quantity

    @property
    def volume(self) -> float:
        return self.unit_volume * self.quantity


@dataclass
class Bay:
    """A cargo bay / position with a longitudinal station (m from datum) used for CoG."""
    name: str
    station: float                   # x-position (m) fwd(+)/aft(-) of datum
    max_weight: float = 1e9          # kg
    max_volume: float = 1e9          # m^3


@dataclass
class Vehicle:
    """The load envelope of the platform."""
    max_payload: float               # kg total cargo
    max_volume: float                # m^3 total cargo
    bays: list = field(default_factory=list)
    cg_fwd_limit: float = 1.5        # m fwd of datum
    cg_aft_limit: float = -1.5       # m aft of datum


@dataclass
class Placement:
    item: Item
    bay: str


class LoadPlanError(ValueError):
    """Raised when a load cannot be placed within the vehicle envelope."""


def plan_load(vehicle: Vehicle, items: Iterable[Item]) -> dict:
    """Pack ``items`` into the vehicle's bays and compute mass properties.

    Greedy heaviest-first placement into the emptiest feasible bay. Returns a manifest
    dict with totals, per-class breakdown, per-bay loading, centre of gravity, and
    feasibility flags. Raises `LoadPlanError` if an item cannot be placed at all.
    """
    items = [i for i in items if i.quantity > 0]
    bays = list(vehicle.bays) or [Bay("main", 0.0, vehicle.max_payload, vehicle.max_volume)]
    bay_w = {b.name: 0.0 for b in bays}
    bay_v = {b.name: 0.0 for b in bays}
    placements: list[Placement] = []

    for it in sorted(items, key=lambda x: (-x.weight, x.name)):
        placed = False
        # prefer the bay with the most remaining weight capacity that fits
        for b in sorted(bays, key=lambda b: (b.max_weight - bay_w[b.name]), reverse=True):
            if (bay_w[b.name] + it.weight <= b.max_weight + 1e-9 and
                    bay_v[b.name] + it.volume <= b.max_volume + 1e-9):
                bay_w[b.name] += it.weight
                bay_v[b.name] += it.volume
                placements.append(Placement(it, b.name))
                placed = True
                break
        if not placed:
            raise LoadPlanError(
                f"cannot place {it.name} ({it.weight:.1f} kg / {it.volume:.3f} m^3) "
                f"in any bay within limits")

    total_w = sum(p.item.weight for p in placements)
    total_v = sum(p.item.volume for p in placements)
    by_class: dict[str, dict] = {}
    for p in placements:
        c = p.item.supply_class
        rec = by_class.setdefault(c, {"class": c, "name": CLASSES[c],
                                      "weight": 0.0, "volume": 0.0, "lines": 0})
        rec["weight"] += p.item.weight
        rec["volume"] += p.item.volume
        rec["lines"] += 1

    station = {b.name: b.station for b in bays}
    moment = sum(bay_w[name] * station[name] for name in bay_w)
    cg = (moment / total_w) if total_w > 0 else 0.0
    cg_ok = vehicle.cg_aft_limit - 1e-9 <= cg <= vehicle.cg_fwd_limit + 1e-9
    payload_ok = total_w <= vehicle.max_payload + 1e-9
    volume_ok = total_v <= vehicle.max_volume + 1e-9

    return {
        "total_weight": round(total_w, 3),
        "total_volume": round(total_v, 4),
        "max_payload": vehicle.max_payload,
        "max_volume": vehicle.max_volume,
        "payload_margin": round(vehicle.max_payload - total_w, 3),
        "volume_margin": round(vehicle.max_volume - total_v, 4),
        "cg": round(cg, 4),
        "cg_ok": cg_ok,
        "payload_ok": payload_ok,
        "volume_ok": volume_ok,
        "feasible": bool(cg_ok and payload_ok and volume_ok),
        "by_class": [by_class[c] for c in sorted(by_class)],
        "bays": [{"bay": b.name, "station": b.station,
                  "weight": round(bay_w[b.name], 3),
                  "volume": round(bay_v[b.name], 4),
                  "weight_pct": round(100 * bay_w[b.name] / b.max_weight, 1)
                  if b.max_weight < 1e8 else None}
                 for b in bays],
        "placements": [{"item": p.item.name, "class": p.item.supply_class,
                        "qty": p.item.quantity, "weight": round(p.item.weight, 3),
                        "bay": p.bay} for p in placements],
        "lines": len(placements),
    }


# ---------------------------------------------------------------------------
# Requirement sizing: how much a formation needs for N days of sustainment.
# Planning factors are coarse per-person/day figures for feasibility studies.
# ---------------------------------------------------------------------------
# (weight kg, volume m^3) per person per day, by class
_PER_PERSON_DAY = {
    "I": (2.0, 0.006),       # rations
    "WATER": (7.5, 0.0075),  # ~7.5 L drinking/day
    "III": (1.0, 0.0012),    # small-unit POL (batteries/stoves/gensets)
    "V": (3.0, 0.004),       # basic load sustainment (cargo)
    "VIII": (0.2, 0.0004),   # medical
    "IX": (0.3, 0.0005),     # repair parts
}

# a US rifle platoon ~ 39, a company HQ ~ 20 (planning approximations)
FORMATIONS = {
    "rifle_platoon": 39,
    "company_hq": 20,
    "rifle_squad": 9,
    "weapons_squad": 9,
}


def requirement(strength: int, days: float = 1.0,
                classes: Optional[Iterable[str]] = None) -> list:
    """Size a resupply requirement for ``strength`` personnel over ``days``.

    Returns a list of `Item` lines (one per class) using coarse planning factors.
    """
    strength = max(0, int(strength))
    days = max(0.0, float(days))
    classes = [c.upper() for c in classes] if classes else list(_PER_PERSON_DAY)
    out = []
    for c in classes:
        if c not in _PER_PERSON_DAY:
            continue
        w, v = _PER_PERSON_DAY[c]
        qty = int(math.ceil(strength * days)) if strength and days else 0
        # model as 'person-days' units so weight/volume scale cleanly
        out.append(Item(name=f"class {c} ({CLASSES[c]})", supply_class=c,
                        unit_weight=round(w, 4), unit_volume=round(v, 5),
                        quantity=qty))
    return out


def platoon_plus_hq(days: float = 3.0) -> list:
    """The CSO benchmark load: a dismounted rifle platoon + a company HQ."""
    strength = FORMATIONS["rifle_platoon"] + FORMATIONS["company_hq"]
    return requirement(strength, days=days)
