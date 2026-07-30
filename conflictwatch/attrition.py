"""attrition — production/attrition trend intelligence for defensive early-warning.

Open sources routinely report two flow figures for a materiel category (armor, tube
artillery, one-way UAS, air-defence interceptors, …): how much is being *produced /
delivered* in a period, and how much is being *lost / attrited*. `trends` and `tempo`
read event streams; this module reads that different, slower signal — the **materiel
balance over time** — and projects the *net force trajectory* forward so an analyst can
see, weeks ahead, whether a side's stock of a system is growing, holding, or drawing
down toward exhaustion.

For a stream of dated :class:`ForceReading` records it will, per (category, side):

  * **trajectory**       — chronological running inventory: each period's produced minus
                           lost (net), the cumulative net, and an estimated absolute
                           inventory anchored to any reported stock figure.
  * **attrition_ratio**  — losses ÷ production over a trailing window; > 1 means the
                           side is being drawn down faster than it is replacing (the
                           core sustainability question).
  * **project**          — a naive least-squares extrapolation of the inventory line
                           ``horizon`` days forward (clamped at zero) with a residual
                           confidence band, plus — when the line is declining — an
                           estimated **depletion date** (when stock would reach zero).
  * **balance**          — the current force ratio between two sides in one category.
  * **board / summary**  — every (category, side) scored the same way and ranked with
                           the soonest-to-deplete, fastest-declining first: an
                           at-a-glance sustainment early-warning list.

Scope: descriptive open-source analysis for awareness, force protection, sustainment and
humanitarian planning. It projects *reported* production and loss figures for human
review — it does not target, task collection, recommend force, or nominate anything, and
the projection is a naive trend line for context, explicitly not a model to act on
blindly. Pure standard library, deterministic, offline.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable, Optional

# trend classes (stable strings used in output + tests), ascending in force level
TREND_CLASSES = ("declining", "stable", "growing")

# defaults (documented so an analyst can audit every number)
DEFAULT_WINDOW = 3        # trailing readings that define the "recent" attrition ratio
DEFAULT_FIT_DAYS = 180    # days of history the inventory trend line is fit to
DEFAULT_HORIZON = 90      # days projected forward
DEFAULT_MIN_POINTS = 2    # min readings in scope before a series is projectable
DEFAULT_TREND = 0.5       # |slope| (units/day) to call growing / declining vs stable
_BAND_K = 1.0             # residual-std multiplier for the projection confidence band


# --------------------------------------------------------------------------- #
# data contract
# --------------------------------------------------------------------------- #
@dataclass
class ForceReading:
    """One dated open-source observation of a materiel category's flow / stock.

    ``produced`` and ``lost`` are the counts attributed to this reading's period (e.g.
    a monthly production estimate and confirmed losses for the same month). ``inventory``
    is an optional reported absolute stock estimate; when present on any reading it
    anchors the trajectory's estimated inventory to a real number. All flow figures are
    coerced non-negative; a bad date leaves ``date`` empty (such rows are skipped).
    """

    date: str = ""                        # ISO-8601 (YYYY-MM-DD)
    category: str = ""                    # system class: "armor", "artillery", "uas" ...
    side: str = ""                        # force / actor the figures describe
    produced: float = 0.0                 # units produced / delivered this period
    lost: float = 0.0                     # units lost / attrited this period
    inventory: Optional[float] = None     # reported absolute stock estimate, if any
    source: str = ""
    notes: str = ""
    tags: list = field(default_factory=list)

    def __post_init__(self):
        self.date = _iso(self.date)
        self.produced = _nonneg(self.produced)
        self.lost = _nonneg(self.lost)
        if self.inventory not in (None, ""):
            try:
                self.inventory = max(0.0, float(self.inventory))
            except (TypeError, ValueError):
                self.inventory = None
        else:
            self.inventory = None
        self.category = str(self.category or "").strip()
        self.side = str(self.side or "").strip()

    @property
    def net(self) -> float:
        """Net change this period (produced − lost); positive = replacing losses."""
        return round(self.produced - self.lost, 4)


# common aliases across OSINT trackers -> canonical field
_ALIASES = {
    "day": "date", "period": "date", "as_of": "date", "timestamp": "date",
    "system": "category", "type": "category", "class": "category", "platform": "category",
    "force": "side", "actor": "side", "party": "side", "belligerent": "side",
    "produced": "produced", "production": "produced", "delivered": "produced",
    "built": "produced", "gained": "produced", "reinforcements": "produced",
    "lost": "lost", "losses": "lost", "attrition": "lost", "destroyed": "lost",
    "casualties": "lost", "attrited": "lost",
    "inventory": "inventory", "stock": "inventory", "holdings": "inventory",
    "fleet": "inventory", "on_hand": "inventory",
    "source": "source", "outlet": "source", "notes": "notes", "description": "notes",
}


def normalize_reading(record: dict, side: str = "") -> ForceReading:
    """Map a loosely-keyed OSINT dict onto a :class:`ForceReading` (alias-aware)."""
    fields = {"side": side} if side else {}
    valid = ForceReading.__dataclass_fields__
    for k, v in record.items():
        kl = str(k).strip().lower()
        canon = _ALIASES.get(kl, kl if kl in valid else None)
        if canon and canon in valid and v not in (None, ""):
            fields[canon] = v
    return ForceReading(**{k: v for k, v in fields.items() if k in valid})


def normalize(records: Iterable[dict], side: str = "") -> list[ForceReading]:
    """Normalize an iterable of dict records, dropping ones without a usable date."""
    out = [normalize_reading(r, side) for r in records]
    return [r for r in out if r.date]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _iso(s) -> str:
    if not s:
        return ""
    try:
        return date.fromisoformat(str(s)[:10]).isoformat()
    except (ValueError, TypeError):
        return ""


def _nonneg(v) -> float:
    try:
        return max(0.0, float(v))
    except (TypeError, ValueError):
        return 0.0


def _parse(d: str) -> Optional[date]:
    try:
        return date.fromisoformat(d)
    except (ValueError, TypeError):
        return None


def _in_scope(r: ForceReading, category: Optional[str], side: Optional[str],
              as_of: Optional[date]) -> bool:
    if not r.date:
        return False
    if category is not None and r.category != category:
        return False
    if side is not None and r.side != side:
        return False
    if as_of is not None:
        d = _parse(r.date)
        if d is None or d > as_of:
            return False
    return True


def _lstsq(xs: list[float], ys: list[float]):
    """Least-squares fit of ``ys`` against ``xs``.

    Returns ``(slope, intercept, resid_std)``; degenerate (n<2 or flat-x) series give
    ``(0, mean(ys), 0)``. ``resid_std`` is the population std of residuals, used only to
    draw a descriptive confidence band.
    """
    n = len(ys)
    if n < 2:
        return 0.0, (ys[0] if ys else 0.0), 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return 0.0, my, 0.0
    slope = sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / denom
    intercept = my - slope * mx
    resid = [ys[i] - (slope * xs[i] + intercept) for i in range(n)]
    resid_std = (sum(r * r for r in resid) / n) ** 0.5
    return slope, intercept, resid_std


def _classify(slope: float, thr: float) -> str:
    t = abs(float(thr))
    if slope >= t:
        return "growing"
    if slope <= -t:
        return "declining"
    return "stable"


def _collapse(readings, category, side, as_of) -> list[dict]:
    """Sum in-scope readings per day into ascending ``{date,d,produced,lost,inv}`` rows.

    ``d`` is the :class:`datetime.date`. ``inv`` is the last non-null reported inventory
    seen on that day (or ``None``). Multiple readings on the same day are folded so the
    trajectory has one point per day.
    """
    by_day: dict[date, dict] = {}
    for r in readings:
        if not _in_scope(r, category, side, as_of):
            continue
        d = _parse(r.date)
        if d is None:
            continue
        cell = by_day.setdefault(d, {"produced": 0.0, "lost": 0.0, "inv": None})
        cell["produced"] += r.produced
        cell["lost"] += r.lost
        if r.inventory is not None:
            cell["inv"] = r.inventory
    out = []
    for d in sorted(by_day):
        c = by_day[d]
        out.append({"date": d.isoformat(), "d": d,
                    "produced": round(c["produced"], 2),
                    "lost": round(c["lost"], 2), "inv": c["inv"]})
    return out


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def scopes(readings: Iterable[ForceReading], *,
           as_of: Optional[str] = None) -> list[dict]:
    """Sorted ``[{category, side}]`` pairs present in the readings (any dated row)."""
    ao = _parse(as_of) if as_of else None
    seen = set()
    for r in readings:
        if _in_scope(r, None, None, ao):
            seen.add((r.category, r.side))
    return [{"category": c, "side": s} for c, s in sorted(seen)]


def trajectory(readings: Iterable[ForceReading], category: str, side: str, *,
               as_of: Optional[str] = None) -> Optional[dict]:
    """Running materiel balance for one (``category``, ``side``) over the reported span.

    Builds one point per reported day: this period's ``produced``/``lost``/``net``, the
    ``cumulative_net`` since the first reading, and ``inventory_est`` — an estimated
    absolute stock. If any reading carries a reported ``inventory`` the earliest such
    value anchors the estimate to a real number; otherwise ``inventory_est`` tracks the
    cumulative net change from the start (``anchored`` is then ``False``). Estimated
    inventory is clamped at zero.

    Returns ``{category, side, anchored, baseline_inventory, points, total_produced,
    total_lost, net_change, current_inventory, span_days}`` or ``None`` when the pair
    has no in-scope readings.
    """
    rows = _collapse(readings, category, side, _parse(as_of) if as_of else None)
    if not rows:
        return None

    # cumulative net across the ordered rows
    cum = 0.0
    for row in rows:
        row["net"] = round(row["produced"] - row["lost"], 2)
        cum = round(cum + row["net"], 2)
        row["cum"] = cum

    # anchor to the first reported absolute inventory, if any
    anchor_i = next((i for i, r in enumerate(rows) if r["inv"] is not None), None)
    anchored = anchor_i is not None
    baseline = rows[anchor_i]["inv"] if anchored else None
    cum_at_anchor = rows[anchor_i]["cum"] if anchored else 0.0

    points = []
    for r in rows:
        if anchored:
            est = baseline + (r["cum"] - cum_at_anchor)
        else:
            est = r["cum"]
        points.append({
            "date": r["date"],
            "produced": r["produced"],
            "lost": r["lost"],
            "net": r["net"],
            "cumulative_net": r["cum"],
            "inventory_est": round(max(0.0, est), 2),
        })

    span = (rows[-1]["d"] - rows[0]["d"]).days
    return {
        "category": category,
        "side": side,
        "anchored": anchored,
        "baseline_inventory": round(baseline, 2) if anchored else None,
        "points": points,
        "total_produced": round(sum(r["produced"] for r in rows), 2),
        "total_lost": round(sum(r["lost"] for r in rows), 2),
        "net_change": round(sum(r["net"] for r in rows), 2),
        "current_inventory": points[-1]["inventory_est"],
        "span_days": span,
    }


def attrition_ratio(readings: Iterable[ForceReading], category: str, side: str, *,
                    window: int = DEFAULT_WINDOW,
                    as_of: Optional[str] = None) -> Optional[dict]:
    """Losses ÷ production over the trailing ``window`` readings for a (category, side).

    A ratio above 1.0 means the side is losing the system faster than it is replacing it
    (``sustainable`` is then ``False``) — the core drawdown question. Returns
    ``{category, side, window, produced, lost, ratio, sustainable, net}`` or ``None``
    when the pair has no in-scope readings. ``ratio`` is ``None`` when nothing was
    produced in the window (undefined — losses against zero production).
    """
    rows = _collapse(readings, category, side, _parse(as_of) if as_of else None)
    if not rows:
        return None
    win = max(1, int(window))
    seg = rows[-win:]
    produced = round(sum(r["produced"] for r in seg), 2)
    lost = round(sum(r["lost"] for r in seg), 2)
    ratio = round(lost / produced, 3) if produced > 0 else None
    return {
        "category": category,
        "side": side,
        "window": len(seg),
        "produced": produced,
        "lost": lost,
        "ratio": ratio,
        "sustainable": (ratio is not None and ratio <= 1.0),
        "net": round(produced - lost, 2),
    }


def project(readings: Iterable[ForceReading], category: str, side: str, *,
            horizon: int = DEFAULT_HORIZON, fit_days: int = DEFAULT_FIT_DAYS,
            min_points: int = DEFAULT_MIN_POINTS, as_of: Optional[str] = None,
            trend_threshold: float = DEFAULT_TREND) -> Optional[dict]:
    """Project the estimated-inventory line ``horizon`` days forward (naive least squares).

    Fits a line to the ``inventory_est`` trajectory over the last ``fit_days`` of history
    and extrapolates day-by-day, clamped at zero, with a residual-based confidence band.
    When the line is declining and current stock is positive it also estimates a
    ``depletion_date`` (when the line would reach zero) and ``days_to_depletion``.

    Returns ``{category, side, anchored, current_inventory, slope_per_day, direction,
    fit_points, horizon_days, projected_inventory, projection, days_to_depletion,
    depletion_date}`` or ``None`` when the pair is missing or has fewer than
    ``min_points`` readings. ``projection`` is ``[{date, value, lo, hi}]``.
    """
    traj = trajectory(readings, category, side, as_of=as_of)
    if traj is None or len(traj["points"]) < max(2, int(min_points)):
        return None

    pts = traj["points"]
    origin = _parse(pts[0]["date"])
    end = _parse(pts[-1]["date"])
    horizon = max(1, int(horizon))

    # restrict the fit to the recent fit_days of history
    cutoff = end - timedelta(days=max(1, int(fit_days)))
    seg = [p for p in pts if _parse(p["date"]) >= cutoff]
    if len(seg) < 2:
        seg = pts[-2:]
    xs = [float((_parse(p["date"]) - origin).days) for p in seg]
    ys = [float(p["inventory_est"]) for p in seg]

    slope, intercept, resid_std = _lstsq(xs, ys)
    band = _BAND_K * resid_std
    x_end = float((end - origin).days)
    current = traj["current_inventory"]

    proj = []
    for h in range(1, horizon + 1):
        x = x_end + h
        val = max(0.0, slope * x + intercept)
        proj.append({
            "date": (end + timedelta(days=h)).isoformat(),
            "value": round(val, 2),
            "lo": round(max(0.0, val - band), 2),
            "hi": round(val + band, 2),
        })

    direction = _classify(slope, trend_threshold)
    days_to_depletion = depletion_date = None
    if slope < 0 and current > 0:
        days = current / (-slope)
        if days >= 0:
            days_to_depletion = round(days, 1)
            depletion_date = (end + timedelta(days=int(round(days)))).isoformat()

    return {
        "category": category,
        "side": side,
        "anchored": traj["anchored"],
        "current_inventory": current,
        "slope_per_day": round(slope, 3),
        "direction": direction,
        "fit_points": len(seg),
        "horizon_days": horizon,
        "projected_inventory": proj[-1]["value"],
        "projection": proj,
        "days_to_depletion": days_to_depletion,
        "depletion_date": depletion_date,
    }


def balance(readings: Iterable[ForceReading], category: str, side_a: str, side_b: str, *,
            as_of: Optional[str] = None) -> Optional[dict]:
    """Current force ratio between two sides in one materiel ``category``.

    Compares each side's latest estimated inventory. ``ratio`` is ``side_a ÷ side_b``
    (``None`` if ``side_b`` is zero). ``leader`` is whichever side currently holds more.
    Returns ``{category, side_a, side_b, inventory_a, inventory_b, ratio, leader,
    advantage}`` or ``None`` when either side has no in-scope readings.
    """
    ta = trajectory(readings, category, side_a, as_of=as_of)
    tb = trajectory(readings, category, side_b, as_of=as_of)
    if ta is None or tb is None:
        return None
    ia = ta["current_inventory"]
    ib = tb["current_inventory"]
    ratio = round(ia / ib, 3) if ib > 0 else None
    if ia > ib:
        leader = side_a
    elif ib > ia:
        leader = side_b
    else:
        leader = None
    return {
        "category": category,
        "side_a": side_a,
        "side_b": side_b,
        "inventory_a": ia,
        "inventory_b": ib,
        "ratio": ratio,
        "leader": leader,
        "advantage": round(abs(ia - ib), 2),
    }


def board(readings: Iterable[ForceReading], *, horizon: int = DEFAULT_HORIZON,
          fit_days: int = DEFAULT_FIT_DAYS, window: int = DEFAULT_WINDOW,
          min_points: int = DEFAULT_MIN_POINTS, as_of: Optional[str] = None,
          trend_threshold: float = DEFAULT_TREND) -> list[dict]:
    """Per (category, side) sustainment board, most at-risk first.

    Every projectable pair gets one row combining its projection and recent attrition
    ratio. Rows are ranked so the soonest-to-deplete come first, then the fastest
    decliners, then the worst attrition ratios — a triage list for defensive planning.

    Returns rows of ``{category, side, direction, current_inventory, slope_per_day,
    days_to_depletion, depletion_date, projected_inventory, attrition_ratio,
    sustainable, anchored}``. Empty when nothing is projectable.
    """
    rows = []
    for sc in scopes(readings, as_of=as_of):
        cat, side = sc["category"], sc["side"]
        pr = project(readings, cat, side, horizon=horizon, fit_days=fit_days,
                     min_points=min_points, as_of=as_of,
                     trend_threshold=trend_threshold)
        if pr is None:
            continue
        ar = attrition_ratio(readings, cat, side, window=window, as_of=as_of) or {}
        rows.append({
            "category": cat,
            "side": side,
            "direction": pr["direction"],
            "current_inventory": pr["current_inventory"],
            "slope_per_day": pr["slope_per_day"],
            "days_to_depletion": pr["days_to_depletion"],
            "depletion_date": pr["depletion_date"],
            "projected_inventory": pr["projected_inventory"],
            "attrition_ratio": ar.get("ratio"),
            "sustainable": ar.get("sustainable"),
            "anchored": pr["anchored"],
        })

    # soonest depletion first (None = never, sorts last); then steepest decline;
    # then worst attrition ratio; then name for a stable order.
    _FAR = float("inf")
    rows.sort(key=lambda r: (
        r["days_to_depletion"] if r["days_to_depletion"] is not None else _FAR,
        r["slope_per_day"],
        -(r["attrition_ratio"] if r["attrition_ratio"] is not None else 0.0),
        r["category"], r["side"],
    ))
    return rows


def summary(readings: Iterable[ForceReading], *, horizon: int = DEFAULT_HORIZON,
            top: int = 5, **kwargs) -> dict:
    """Compact production/attrition roll-up over every projectable (category, side).

    Returns ``{as_of, horizon, series, declining, stable, growing, at_risk,
    total_produced, total_lost, net_change, at_risk_series, board}`` — a one-glance
    sustainment card: how many series are drawing down, how many would deplete within the
    horizon (``at_risk``), the aggregate produced/lost/net across all series, and the
    most at-risk series first.
    """
    as_of = kwargs.get("as_of")
    b = board(readings, horizon=horizon, as_of=as_of,
              **{k: v for k, v in kwargs.items() if k != "as_of"})
    counts = {c: 0 for c in TREND_CLASSES}
    for r in b:
        counts[r["direction"]] += 1

    total_p = total_l = net = 0.0
    for sc in scopes(readings, as_of=as_of):
        tr = trajectory(readings, sc["category"], sc["side"], as_of=as_of)
        if tr:
            total_p += tr["total_produced"]
            total_l += tr["total_lost"]
            net += tr["net_change"]

    at_risk = [r for r in b if r["days_to_depletion"] is not None
               and r["days_to_depletion"] <= horizon]
    n = max(0, int(top))
    return {
        "as_of": as_of,
        "horizon": horizon,
        "series": len(b),
        "declining": counts["declining"],
        "stable": counts["stable"],
        "growing": counts["growing"],
        "at_risk": len(at_risk),
        "total_produced": round(total_p, 2),
        "total_lost": round(total_l, 2),
        "net_change": round(net, 2),
        "at_risk_series": [{"category": r["category"], "side": r["side"],
                            "days_to_depletion": r["days_to_depletion"],
                            "depletion_date": r["depletion_date"]}
                           for r in at_risk[:n]],
        "board": b,
    }
