"""routeplan — exposure-aware contested-route planner for the last tactical mile.

Getting cargo or casualties across the last mile is not about the *shortest* path; it is
about the *least exposed* path that a wheeled/tracked platform can actually drive. This
module plans routes over a cost grid that blends four defensive layers:

  * **traversability** — per-cell mobility cost (slope, surface); impassable cells blocked;
  * **exposure / intervisibility** — a viewshed cost: cells visible from known observer
    points (ridgelines, likely enemy overwatch) cost more, so the planner hugs defilade;
  * **hazard overlay** — operator-marked keep-out / danger areas;
  * **smoothness** — a turn penalty so casualty routes prefer gentle corridors.

Planning uses A* with an admissible distance heuristic. The output is a driveable path
plus an exposure profile the crew can inspect. This is purely *defensive route selection*
for the vehicle's own movement: it avoids being seen. It does not locate, identify, aim
at, or engage anything — observer points are inputs the operator supplies to model where
the vehicle might be watched *from*. Pure stdlib, deterministic, offline.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

# 8-connected moves: (di, dj, step-cost-multiplier)
_MOVES = [(-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0),
          (-1, -1, math.sqrt(2)), (-1, 1, math.sqrt(2)),
          (1, -1, math.sqrt(2)), (1, 1, math.sqrt(2))]

BLOCKED = float("inf")


@dataclass
class Grid:
    """A square-cell cost grid. ``cost[j][i]`` is the base mobility cost to enter cell
    (i, j); use `BLOCKED` (inf) for impassable. Optional ``elevation`` enables viewshed.
    """
    cost: list
    cell: float = 10.0               # metres per cell
    elevation: Optional[list] = None

    def __post_init__(self):
        self.rows = len(self.cost)
        self.cols = len(self.cost[0]) if self.rows else 0
        if self.rows < 1 or self.cols < 1:
            raise ValueError("grid must be non-empty")
        for row in self.cost:
            if len(row) != self.cols:
                raise ValueError("grid rows must be equal length")

    def in_bounds(self, i: int, j: int) -> bool:
        return 0 <= i < self.cols and 0 <= j < self.rows

    def passable(self, i: int, j: int) -> bool:
        return self.in_bounds(i, j) and self.cost[j][i] != BLOCKED


def add_exposure(grid: Grid, observers: Iterable[tuple], radius_cells: int = 12,
                 weight: float = 5.0, observer_height: float = 2.0) -> list:
    """Compute an exposure layer: for each cell, ``weight`` if visible from any observer.

    Visibility uses a line-of-sight elevation check when ``grid.elevation`` is present
    (a cell is hidden if terrain between it and the observer rises above the sightline),
    otherwise a plain radius. Returns a rows×cols additive-cost layer.
    """
    exp = [[0.0] * grid.cols for _ in range(grid.rows)]
    obs = list(observers)
    for (oi, oj) in obs:
        for dj in range(-radius_cells, radius_cells + 1):
            for di in range(-radius_cells, radius_cells + 1):
                i, j = oi + di, oj + dj
                if not grid.in_bounds(i, j):
                    continue
                if di * di + dj * dj > radius_cells * radius_cells:
                    continue
                if _visible(grid, oi, oj, i, j, observer_height):
                    exp[j][i] = weight
    return exp


def _visible(grid: Grid, oi: int, oj: int, ti: int, tj: int,
             observer_height: float) -> bool:
    """Bresenham line-of-sight with a simple terrain-mask elevation test."""
    if grid.elevation is None:
        return True
    z_obs = grid.elevation[oj][oi] + observer_height
    z_tgt = grid.elevation[tj][ti]
    line = list(_bresenham(oi, oj, ti, tj))
    n = len(line)
    if n <= 2:
        return True
    for k, (i, j) in enumerate(line[1:-1], start=1):
        frac = k / (n - 1)
        sight_z = z_obs + (z_tgt - z_obs) * frac
        if grid.elevation[j][i] > sight_z + 1e-9:
            return False
    return True


def _bresenham(x0, y0, x1, y1):
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        yield (x0, y0)
        if x0 == x1 and y0 == y1:
            return
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


@dataclass
class Route:
    cells: list                      # [(i, j), ...]
    total_cost: float
    length_m: float
    max_exposure: float
    mean_exposure: float
    found: bool = True

    def as_dict(self) -> dict:
        return {"found": self.found, "cells": [list(c) for c in self.cells],
                "total_cost": round(self.total_cost, 3),
                "length_m": round(self.length_m, 2),
                "max_exposure": round(self.max_exposure, 3),
                "mean_exposure": round(self.mean_exposure, 3),
                "waypoints": len(self.cells)}


def plan_route(grid: Grid, start: tuple, goal: tuple,
               exposure: Optional[list] = None, exposure_weight: float = 1.0,
               turn_penalty: float = 0.0) -> Route:
    """A* least-cost route from ``start`` to ``goal`` (both (i, j) cell tuples).

    Cell entry cost = base mobility cost + exposure_weight * exposure[cell]. A diagonal
    move multiplies by sqrt(2). ``turn_penalty`` (>=0) discourages heading changes for a
    smoother casualty-transport corridor. Returns a `Route`; ``found=False`` if blocked.
    """
    si, sj = start
    gi, gj = goal
    if not grid.passable(si, sj):
        raise ValueError("start cell is blocked or out of bounds")
    if not grid.passable(gi, gj):
        raise ValueError("goal cell is blocked or out of bounds")
    exposure_weight = max(0.0, float(exposure_weight))
    turn_penalty = max(0.0, float(turn_penalty))

    def h(i, j):
        # octile distance heuristic (admissible for 8-connected unit costs)
        dx, dy = abs(i - gi), abs(j - gj)
        return (max(dx, dy) + (math.sqrt(2) - 1) * min(dx, dy))

    start_state = (si, sj, 0)          # include incoming direction index (0 = none)
    open_heap = [(h(si, sj), 0.0, si, sj, -1)]
    best = {(si, sj): 0.0}
    came = {}
    dir_of = {(si, sj): -1}

    while open_heap:
        _, g, i, j, pdir = heapq.heappop(open_heap)
        if (i, j) == (gi, gj):
            return _reconstruct(grid, came, (i, j), (si, sj), exposure, exposure_weight)
        if g > best.get((i, j), BLOCKED) + 1e-9:
            continue
        for k, (di, dj, mult) in enumerate(_MOVES):
            ni, nj = i + di, j + dj
            if not grid.passable(ni, nj):
                continue
            base = grid.cost[nj][ni]
            exp = exposure[nj][ni] if exposure else 0.0
            step = base * mult + exposure_weight * exp * mult
            if pdir >= 0 and k != pdir and turn_penalty:
                step += turn_penalty
            ng = g + step
            if ng + 1e-12 < best.get((ni, nj), BLOCKED):
                best[(ni, nj)] = ng
                came[(ni, nj)] = (i, j)
                heapq.heappush(open_heap, (ng + h(ni, nj), ng, ni, nj, k))
    return Route([], BLOCKED, 0.0, 0.0, 0.0, found=False)


def _reconstruct(grid, came, goal, start, exposure, ew) -> Route:
    cells = [goal]
    cur = goal
    while cur != start:
        cur = came[cur]
        cells.append(cur)
    cells.reverse()
    length = 0.0
    total = 0.0
    exps = []
    prev = None
    for (i, j) in cells:
        exp = exposure[j][i] if exposure else 0.0
        exps.append(exp)
        if prev is not None:
            di, dj = i - prev[0], j - prev[1]
            seg = grid.cell * (math.sqrt(2) if di and dj else 1.0)
            length += seg
            total += grid.cost[j][i] * (math.sqrt(2) if di and dj else 1.0)
            total += ew * exp
        prev = (i, j)
    return Route(cells, total, length,
                 max(exps) if exps else 0.0,
                 (sum(exps) / len(exps)) if exps else 0.0, found=True)
