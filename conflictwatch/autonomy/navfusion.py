"""navfusion — GPS-denied navigation fusion core for an autonomous ground vehicle.

The "last tactical mile" routinely runs through areas where GNSS is jammed, spoofed,
or simply masked by terrain. A sustainment/CASEVAC UGV still has to know *where it is*
well enough to follow a route. This module fuses the position sources a vehicle already
carries — IMU dead-reckoning, camera visual-inertial odometry, LiDAR-inertial SLAM, and
terrain-relative matching against a pre-loaded elevation map — into one estimate that
**degrades gracefully as GNSS drops out** instead of failing hard.

The design is deliberately small and auditable:

  * a 2-D local-frame state (east, north, heading) with a diagonal position covariance;
  * a *predict* step that dead-reckons from odometry and inflates covariance by a stated
    process-noise rate (drift grows the longer you go without a fix);
  * an *update* step that fuses any position `Fix` (GNSS / VIO / LiDAR-SLAM / terrain)
    with a scalar Kalman gain per axis, weighting each source by its reported variance;
  * a `TerrainMap` that turns a short measured elevation profile into a position `Fix`
    when GNSS is unavailable — the classic terrain-relative-navigation fallback.

This is *navigation only*: it estimates the vehicle's own position so it can drive a
route. It does not locate, track, aim at, or guide anything toward a target. Pure stdlib,
deterministic, offline.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

# position-fix sources, best-known accuracy first (documentation / validation only)
SOURCES = ("gnss", "lidar_slam", "vio", "terrain", "manual")


@dataclass
class Fix:
    """A position measurement in the local ENU frame (metres), with its variance."""
    x: float                       # east (m)
    y: float                       # north (m)
    var: float                     # measurement variance (m^2), same for both axes
    source: str = "manual"
    t: float = 0.0                 # timestamp (s), monotonic; informational

    def __post_init__(self):
        self.x = float(self.x)
        self.y = float(self.y)
        self.var = max(1e-6, float(self.var))
        if self.source not in SOURCES:
            self.source = "manual"


@dataclass
class State:
    """The fused navigation estimate at an instant."""
    x: float = 0.0
    y: float = 0.0
    heading: float = 0.0           # radians, 0 = east, CCW positive
    var_x: float = 25.0
    var_y: float = 25.0
    t: float = 0.0
    last_fix: Optional[str] = None
    fixes: int = 0

    @property
    def pos_sigma(self) -> float:
        """1-sigma position uncertainty (m), circular-equivalent."""
        return math.sqrt(max(0.0, (self.var_x + self.var_y) / 2.0))

    def as_dict(self) -> dict:
        return {"x": round(self.x, 3), "y": round(self.y, 3),
                "heading_deg": round(math.degrees(self.heading) % 360.0, 2),
                "pos_sigma": round(self.pos_sigma, 3),
                "last_fix": self.last_fix, "fixes": self.fixes,
                "t": round(self.t, 3)}


def wrap_angle(a: float) -> float:
    """Wrap radians to (-pi, pi]."""
    a = math.fmod(a, 2 * math.pi)
    if a <= -math.pi:
        a += 2 * math.pi
    elif a > math.pi:
        a -= 2 * math.pi
    return a


class Navigator:
    """A degrade-gracefully position estimator fusing odometry with position fixes.

    Usage::

        nav = Navigator(process_noise=0.5)          # m^2 growth per second
        nav.predict(speed=2.0, heading_rate=0.0, dt=1.0)   # dead-reckon 2 m east
        nav.update(Fix(x=1.9, y=0.1, var=4.0, source="vio"))
    """

    def __init__(self, x: float = 0.0, y: float = 0.0, heading: float = 0.0,
                 pos_var: float = 25.0, process_noise: float = 0.5,
                 max_var: float = 1.0e6):
        self.state = State(x=x, y=y, heading=heading, var_x=pos_var, var_y=pos_var)
        self.process_noise = max(0.0, float(process_noise))
        self.max_var = float(max_var)
        self._t = 0.0

    # -- prediction (dead-reckoning) -----------------------------------------
    def predict(self, speed: float = 0.0, heading_rate: float = 0.0,
                dt: float = 1.0) -> State:
        """Advance the estimate by odometry over ``dt`` seconds.

        ``speed`` is m/s along the current heading; ``heading_rate`` is rad/s. The
        position covariance grows by ``process_noise * dt`` on each axis, so the
        estimate steadily loses confidence until a `Fix` is fused.
        """
        dt = max(0.0, float(dt))
        s = self.state
        # rotate then translate (mid-point heading for a smoother arc)
        h0 = s.heading
        s.heading = wrap_angle(h0 + heading_rate * dt)
        h_mid = wrap_angle(h0 + heading_rate * dt / 2.0)
        dist = speed * dt
        s.x += dist * math.cos(h_mid)
        s.y += dist * math.sin(h_mid)
        grow = self.process_noise * dt
        s.var_x = min(self.max_var, s.var_x + grow)
        s.var_y = min(self.max_var, s.var_y + grow)
        self._t += dt
        s.t = self._t
        return s

    # -- correction (fuse a fix) ---------------------------------------------
    def update(self, fix: Fix) -> State:
        """Fuse a position `Fix` with a scalar Kalman update per axis."""
        s = self.state
        for axis, meas in (("x", fix.x), ("y", fix.y)):
            var = getattr(s, "var_" + axis)
            k = var / (var + fix.var)                     # Kalman gain 0..1
            cur = getattr(s, axis)
            setattr(s, axis, cur + k * (meas - cur))
            setattr(s, "var_" + axis, (1.0 - k) * var)
        s.last_fix = fix.source
        s.fixes += 1
        return s

    def gnss_dropout(self) -> None:
        """Marker for a lost GNSS lock — no state change, documents intent in logs."""
        # position keeps propagating on odometry + non-GNSS fixes; nothing to reset.
        return None

    def quality(self) -> float:
        """Navigation quality 0..1 from position sigma (1 m -> ~1.0, 100 m -> ~0)."""
        sig = self.state.pos_sigma
        # smooth, bounded: 1 / (1 + (sigma/10)^2)
        return round(1.0 / (1.0 + (sig / 10.0) ** 2), 4)

    def mode(self, gnss_gap_fixes: int = 0) -> str:
        """Report the operating mode from the most recent fix source."""
        lf = self.state.last_fix
        if lf == "gnss":
            return "gnss"
        if lf in ("lidar_slam", "vio"):
            return "odometry-aided"
        if lf == "terrain":
            return "terrain-relative"
        return "dead-reckoning"


# ---------------------------------------------------------------------------
# Terrain-relative navigation: match a measured elevation profile to a map.
# ---------------------------------------------------------------------------
@dataclass
class TerrainMap:
    """A regular elevation grid in the local ENU frame.

    ``grid[j][i]`` is the elevation (m) at east = origin_x + i*cell,
    north = origin_y + j*cell. Bilinear sampling; used to turn a short measured
    elevation profile into a position `Fix` when GNSS is denied.
    """
    grid: Sequence[Sequence[float]]
    cell: float = 10.0
    origin_x: float = 0.0
    origin_y: float = 0.0

    def __post_init__(self):
        self.rows = len(self.grid)
        self.cols = len(self.grid[0]) if self.rows else 0
        if self.rows < 2 or self.cols < 2:
            raise ValueError("TerrainMap needs at least a 2x2 grid")
        self.cell = float(self.cell)

    def elevation_at(self, x: float, y: float) -> float:
        """Bilinear elevation at world (x, y); clamps to grid bounds."""
        fx = (x - self.origin_x) / self.cell
        fy = (y - self.origin_y) / self.cell
        i0 = min(max(int(math.floor(fx)), 0), self.cols - 2)
        j0 = min(max(int(math.floor(fy)), 0), self.rows - 2)
        tx = min(max(fx - i0, 0.0), 1.0)
        ty = min(max(fy - j0, 0.0), 1.0)
        z00 = self.grid[j0][i0]
        z10 = self.grid[j0][i0 + 1]
        z01 = self.grid[j0 + 1][i0]
        z11 = self.grid[j0 + 1][i0 + 1]
        a = z00 * (1 - tx) + z10 * tx
        b = z01 * (1 - tx) + z11 * tx
        return a * (1 - ty) + b * ty

    def match_profile(self, samples: Sequence[tuple], guess_x: float, guess_y: float,
                      search: float = 40.0, step: Optional[float] = None) -> Fix:
        """Find the position whose elevation profile best fits ``samples``.

        ``samples`` is a sequence of ``(dx, dy, elevation)`` — relative offsets from the
        vehicle and the measured ground elevation there (from LiDAR/baro). A grid search
        around (guess_x, guess_y) minimises sum-of-squared elevation error; the residual
        sets the fix variance, so a crisp match is trusted and a vague one is not.
        """
        if not samples:
            raise ValueError("need at least one elevation sample")
        step = step if step else self.cell / 2.0
        best = None
        best_sse = None
        n = max(1, int(round(search / step)))
        for jj in range(-n, n + 1):
            for ii in range(-n, n + 1):
                cx = guess_x + ii * step
                cy = guess_y + jj * step
                sse = 0.0
                for dx, dy, z in samples:
                    est = self.elevation_at(cx + dx, cy + dy)
                    sse += (est - z) ** 2
                if best_sse is None or sse < best_sse - 1e-12 or (
                        abs(sse - best_sse) <= 1e-12 and (cx, cy) < best):
                    best_sse = sse
                    best = (cx, cy)
        rms = math.sqrt(best_sse / len(samples))
        # variance grows with residual and shrinks with sample count
        var = max(1.0, (rms + 1.0) ** 2 * 4.0 / len(samples))
        return Fix(x=best[0], y=best[1], var=var, source="terrain")


def dead_reckon(path: Sequence[tuple], start: Optional[State] = None,
                process_noise: float = 0.5) -> State:
    """Convenience: dead-reckon a whole ``(speed, heading_rate, dt)`` sequence."""
    nav = Navigator(process_noise=process_noise)
    if start:
        nav.state = start
    for speed, hr, dt in path:
        nav.predict(speed=speed, heading_rate=hr, dt=dt)
    return nav.state
