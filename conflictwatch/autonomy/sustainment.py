"""sustainment — platoon/company demand forecasting & resupply triggering.

A resupply run should launch *before* a supported unit goes black on a critical supply,
not after. This module tracks consumption of the key supply classes for a formation,
projects when each class will cross a re-order trigger, and emits resupply requests sized
to restore a target days-of-supply — feeding straight into the load-planning engine.

It models, per supply class, an on-hand quantity (in person-days of supply), a consumption
rate, and two thresholds:

  * **trigger** — days-of-supply at which a resupply request is raised (re-order point);
  * **critical** — days-of-supply below which the unit is effectively black.

Given a formation strength it converts these to concrete quantities, forecasts the day
each class hits its trigger, and orders enough to reach the target level. This is pure
sustainment analytics — consumption accounting and reorder logic. No targeting, no
operational tasking. Pure stdlib, deterministic, offline.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional

from conflictwatch.autonomy.loadplan import Item, CLASSES, _PER_PERSON_DAY


@dataclass
class ClassStock:
    """On-hand stock and reorder policy for one supply class, in days-of-supply."""
    supply_class: str
    days_on_hand: float              # current days of supply
    daily_burn_mult: float = 1.0     # consumption vs the nominal planning factor
    trigger_days: float = 2.0        # re-order at this many days remaining
    critical_days: float = 1.0       # 'black' below this
    target_days: float = 5.0         # resupply back up to this

    def __post_init__(self):
        self.supply_class = str(self.supply_class).upper()
        if self.supply_class not in CLASSES:
            raise ValueError(f"unknown supply class {self.supply_class!r}")
        self.days_on_hand = max(0.0, float(self.days_on_hand))
        self.daily_burn_mult = max(0.0, float(self.daily_burn_mult))


def days_until_trigger(stock: ClassStock) -> float:
    """How many days until this class hits its re-order trigger (0 if already there)."""
    if stock.daily_burn_mult <= 0:
        return float("inf")
    remaining = stock.days_on_hand - stock.trigger_days
    # days_on_hand is measured in *nominal* days; burn_mult accelerates depletion
    return max(0.0, remaining / stock.daily_burn_mult)


def status_of(stock: ClassStock) -> str:
    """Classify a stock: black / critical / reorder / green."""
    effective = stock.days_on_hand / stock.daily_burn_mult if stock.daily_burn_mult else float("inf")
    if effective <= 0:
        return "black"
    if effective <= stock.critical_days:
        return "critical"
    if effective <= stock.trigger_days:
        return "reorder"
    return "green"


def forecast(stocks: Iterable[ClassStock], horizon_days: float = 7.0) -> list:
    """Project each class forward; sort most-urgent first."""
    out = []
    for s in stocks:
        eff = s.days_on_hand / s.daily_burn_mult if s.daily_burn_mult else float("inf")
        out.append({
            "class": s.supply_class,
            "name": CLASSES[s.supply_class],
            "days_on_hand": round(s.days_on_hand, 3),
            "effective_days": round(eff, 3) if eff != float("inf") else None,
            "status": status_of(s),
            "days_to_trigger": round(days_until_trigger(s), 3),
            "will_trigger_within_horizon": days_until_trigger(s) <= horizon_days,
            "black_within_horizon": eff <= horizon_days,
        })
    order = {"black": 0, "critical": 1, "reorder": 2, "green": 3}
    out.sort(key=lambda r: (order[r["status"]], r["days_to_trigger"]))
    return out


def resupply_request(stocks: Iterable[ClassStock], strength: int,
                     horizon_days: float = 7.0) -> dict:
    """Build a resupply request for classes at/near trigger.

    For each class whose re-order point will be hit inside ``horizon_days``, order enough
    to restore ``target_days`` of supply, sized to ``strength`` personnel. Returns the
    request plus `Item` lines ready for `loadplan.plan_load`.
    """
    strength = max(0, int(strength))
    items: list[Item] = []
    lines = []
    for s in stocks:
        if days_until_trigger(s) > horizon_days and status_of(s) == "green":
            continue
        deficit_days = max(0.0, s.target_days - s.days_on_hand)
        if deficit_days <= 0:
            continue
        c = s.supply_class
        factor = _PER_PERSON_DAY.get(c)
        if not factor:
            continue
        w, v = factor
        qty = int(math.ceil(strength * deficit_days))
        if qty <= 0:
            continue
        item = Item(name=f"resupply class {c}", supply_class=c,
                    unit_weight=round(w, 4), unit_volume=round(v, 5), quantity=qty)
        items.append(item)
        lines.append({"class": c, "status": status_of(s),
                      "deficit_days": round(deficit_days, 3),
                      "person_days": qty, "weight": round(item.weight, 2)})
    return {
        "strength": strength,
        "horizon_days": horizon_days,
        "requested": len(items),
        "lines": lines,
        "items": items,
        "total_weight": round(sum(i.weight for i in items), 2),
        "urgent": any(l["status"] in ("black", "critical") for l in lines),
    }
