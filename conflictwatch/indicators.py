"""indicators — a defensive Indications & Warning (I&W) posture per scope.

Force protection and humanitarian staff don't need another raw number; they need a
*posture*: given everything the open picture is saying about a place, how alert
should people there be, and *why*. This module rolls the descriptive signals the
rest of conflictwatch already produces into a single, auditable advisory tier per
scope — GREEN / AMBER / RED (plus GUARDED between) — built from five transparent,
bounded sub-scores:

  * **tempo**      — recent event rate vs the trailing baseline (are things busier?)
  * **lethality**  — reported fatalities-per-event (how deadly, not just how often)
  * **escalation** — count/severity of `watch` early-warning alerts for the scope
  * **drone/uas**  — share of recent activity that is drone/UAS or explosive/remote
                     (the threats force-protection SOPs care about most)
  * **spread**     — how many distinct places are active (concentrated vs diffuse)

Each sub-score is 0..1 with a stated reason; the composite maps to a tier and a set
of **defensive, descriptive advisories** (increase dispersion, review overhead cover,
brief the drone threat, etc.) drawn from the "what's working" lessons vocabulary.

This is an *advisory for humans*, derived only from reported open-source events. It
does NOT target, task collection, plan operations, or recommend force — it tells
people how cautious to be and what protective measures the open lessons suggest.
Pure standard library, deterministic, offline.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Iterable, Optional

from conflictwatch.events import ConflictEvent
from conflictwatch import watch

# advisory tiers, ascending alertness
TIERS = ("green", "guarded", "amber", "red")

# sub-scores that make up the composite (stable keys used in output + tests)
FACTORS = ("tempo", "lethality", "escalation", "drone_uas", "spread")

# descriptive, defensive advisories keyed to which factor is driving alertness.
# awareness / force-protection only — no offensive or targeting content.
_ADVISORIES = {
    "tempo": "Activity tempo is rising — refresh the local picture more often and "
             "brief movement plans against the latest reporting.",
    "lethality": "Reported incidents are getting deadlier — review overhead cover, "
                 "dispersion, and casualty-care readiness for the area.",
    "escalation": "Early-warning detectors are firing — treat the situation as "
                  "changing, not steady, and shorten your reassessment cadence.",
    "drone_uas": "Drone/UAS and explosive-remote activity is prominent — brief the "
                 "small-drone threat, watch overhead, and review counter-UAS SOPs "
                 "(see the counter-UAS KB).",
    "spread": "Activity is diffusing across multiple places — a single safe corridor "
              "assumption may no longer hold; re-check routes and staging areas.",
}


def _parse(d: str) -> Optional[date]:
    try:
        return date.fromisoformat(d)
    except (ValueError, TypeError):
        return None


def _scope_of(e: ConflictEvent, scope: str) -> str:
    if scope == "country":
        return e.country or "(unknown)"
    if scope == "region":
        return f"{e.country}/{e.region}" if e.region else (e.country or "(unknown)")
    if scope == "location":
        return e.location or e.region or e.country or "(unknown)"
    if scope == "global":
        return "(all)"
    raise ValueError(f"unknown scope {scope!r}; expected country/region/location/global")


def _clamp(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else round(float(x), 3)


def _tier_from(score: float) -> str:
    if score >= 0.75:
        return "red"
    if score >= 0.5:
        return "amber"
    if score >= 0.25:
        return "guarded"
    return "green"


def posture(events: Iterable[ConflictEvent], *, scope: str = "country",
            window: int = 7, baseline_windows: int = 4,
            as_of: Optional[str] = None) -> list[dict]:
    """Compute a defensive I&W posture per scope over a recent window.

    Returns one record per scope with activity in the recent window, sorted by
    composite score (most-alert first), each::

        {scope, tier, score, factors:{name:{score,reason}}, advisories:[...],
         recent_events, recent_fatalities, window}
    """
    evs = [e for e in events if _parse(e.date)]
    if not evs:
        return []
    ref = _parse(as_of) if (as_of and _parse(as_of)) else max(_parse(e.date) for e in evs)
    win = max(1, int(window))
    base_days = win * max(1, int(baseline_windows))
    recent_lo = ref - timedelta(days=win - 1)
    base_lo = recent_lo - timedelta(days=base_days)
    base_hi = recent_lo - timedelta(days=1)

    # pre-compute per-scope early-warning alert counts (weighted by severity)
    alerts = watch.detect(evs, scope=scope, window=win,
                           baseline_windows=baseline_windows, as_of=as_of)
    esc_weight = defaultdict(float)
    sev_w = {"info": 0.0, "low": 0.3, "medium": 0.6, "high": 0.85, "critical": 1.0}
    for a in alerts:
        esc_weight[a["scope"]] = max(esc_weight[a["scope"]], sev_w.get(a["severity"], 0.0))

    groups: dict[str, list[ConflictEvent]] = defaultdict(list)
    for e in evs:
        groups[_scope_of(e, scope)].append(e)

    out = []
    for name, group in groups.items():
        recent = [e for e in group if recent_lo <= _parse(e.date) <= ref]
        base = [e for e in group if base_lo <= _parse(e.date) <= base_hi]
        if not recent:
            continue
        rec_n = len(recent)
        rec_fat = sum(e.fatalities for e in recent)
        base_n = len(base)

        # --- tempo: recent window rate vs baseline per-window rate --------------
        base_per_win = (base_n / max(1, int(baseline_windows)))
        if base_per_win <= 0:
            tempo = 1.0 if rec_n >= 4 else min(rec_n / 4.0, 1.0)
            tempo_reason = f"{rec_n} events with no baseline activity"
        else:
            ratio = rec_n / base_per_win
            tempo = _clamp((ratio - 1.0) / 2.0)  # 1x->0, 3x->1
            tempo_reason = (f"{rec_n} events this window vs ~{base_per_win:.1f}/window "
                            f"baseline ({ratio:.1f}x)")

        # --- lethality: fatalities per recent event ----------------------------
        lph = rec_fat / rec_n if rec_n else 0.0
        lethality = _clamp(lph / 5.0)  # 5 fatalities/event -> saturated
        leth_reason = f"{lph:.1f} reported fatalities/event ({rec_fat} over {rec_n})"

        # --- escalation: early-warning severity for this scope -----------------
        escalation = _clamp(esc_weight.get(name, 0.0))
        esc_reason = (f"early-warning severity weight {escalation:.2f}"
                      if escalation else "no early-warning alerts")

        # --- drone/uas + explosive share of recent activity -------------------
        threat_types = {"drone/uas", "explosion/remote"}
        threat_n = sum(1 for e in recent if e.event_type in threat_types)
        drone_uas = _clamp(threat_n / rec_n) if rec_n else 0.0
        drone_reason = f"{threat_n}/{rec_n} recent events are drone/UAS or explosive-remote"

        # --- spread: distinct active places ------------------------------------
        places = {(e.location or e.region or e.country) for e in recent
                  if (e.location or e.region or e.country)}
        spread = _clamp((len(places) - 1) / 5.0)  # 1 place->0, 6 places->1
        spread_reason = f"{len(places)} distinct active place(s) this window"

        factors = {
            "tempo": {"score": tempo, "reason": tempo_reason},
            "lethality": {"score": lethality, "reason": leth_reason},
            "escalation": {"score": escalation, "reason": esc_reason},
            "drone_uas": {"score": drone_uas, "reason": drone_reason},
            "spread": {"score": spread, "reason": spread_reason},
        }
        # weighted composite (escalation + lethality weighted highest — they carry
        # the most force-protection signal); weights sum to 1.
        weights = {"tempo": 0.2, "lethality": 0.25, "escalation": 0.3,
                   "drone_uas": 0.15, "spread": 0.1}
        score = _clamp(sum(factors[f]["score"] * weights[f] for f in FACTORS))
        tier = _tier_from(score)

        # advisories: surface the drivers (factor score above a floor), worst-first
        drivers = sorted(FACTORS, key=lambda f: factors[f]["score"], reverse=True)
        advisories = [_ADVISORIES[f] for f in drivers if factors[f]["score"] >= 0.34]

        out.append({
            "scope": name,
            "tier": tier,
            "score": score,
            "factors": factors,
            "advisories": advisories,
            "recent_events": rec_n,
            "recent_fatalities": rec_fat,
            "window": win,
        })
    out.sort(key=lambda r: (r["score"], r["recent_fatalities"]), reverse=True)
    return out


def summary(events, **kwargs) -> dict:
    """Roll-up of postures: counts by tier + the highest-alert scope."""
    postures = posture(events, **kwargs)
    by_tier = Counter(p["tier"] for p in postures)
    top = postures[0] if postures else None
    return {
        "scopes": len(postures),
        "by_tier": {t: by_tier.get(t, 0) for t in reversed(TIERS) if by_tier.get(t)},
        "highest": top["tier"] if top else "green",
        "top": top,
        "postures": postures,
    }
