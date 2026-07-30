"""shadowgeo — highlight/shadow geometry feature extractor for side-scan sonar.

Side-scan and synthetic-aperture sonar image the seabed as a *waterfall*: a proud object
returns a bright **highlight**, and the insonified energy it blocks leaves an **acoustic
shadow** behind it, away from the sonar track. The shadow's length, together with the
sonar's altitude and the object's range, gives the object's *height* by simple similar-
triangles geometry — a physics-grounded, explainable feature that complements a learned
classifier and, importantly, gives a human analyst an auditable rationale (deep nets alone
are prone to background/domain bias on the scarce sonar training data).

This module is deterministic image geometry only: it segments a highlight and its trailing
shadow and derives height, length, and aspect. It reads sonar imagery for object
detection/survey. It has no weapon, fuzing, or engagement function of any kind. Pure
stdlib, deterministic, offline.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Optional, Sequence


@dataclass
class Waterfall:
    """A side-scan intensity image. ``pixels[j][i]`` is the return intensity at
    along-track row ``j`` and across-track (range) column ``i``; column 0 is nearest the
    nadir/track and columns increase with ground range (shadows fall toward higher ``i``).
    """
    pixels: list
    across_res: float = 0.1          # metres per across-track pixel (range)
    along_res: float = 0.1           # metres per along-track pixel
    altitude: float = 10.0           # sonar height above seabed (m)

    def __post_init__(self):
        self.rows = len(self.pixels)
        self.cols = len(self.pixels[0]) if self.rows else 0
        if self.rows < 1 or self.cols < 1:
            raise ValueError("waterfall must be non-empty")
        for r in self.pixels:
            if len(r) != self.cols:
                raise ValueError("waterfall rows must be equal length")

    def ground_range(self, i: int) -> float:
        return i * self.across_res


def _stats(wf: Waterfall) -> tuple:
    flat = [v for row in wf.pixels for v in row]
    mean = sum(flat) / len(flat)
    sd = statistics.pstdev(flat) if len(flat) > 1 else 0.0
    return mean, sd


@dataclass
class ShadowFeature:
    """Geometry derived for one highlight + its trailing shadow."""
    row_center: int
    col_highlight_end: int           # last (farthest) column of the highlight
    shadow_len_px: int
    shadow_len_m: float
    object_range_m: float
    object_height_m: float
    object_length_m: float
    object_width_m: float
    aspect: float                    # length / width
    has_shadow: bool
    quality: float                   # 0..1 confidence in the shadow measurement

    def as_dict(self) -> dict:
        return {"row_center": self.row_center,
                "shadow_len_m": round(self.shadow_len_m, 4),
                "object_range_m": round(self.object_range_m, 3),
                "object_height_m": round(self.object_height_m, 4),
                "object_length_m": round(self.object_length_m, 4),
                "object_width_m": round(self.object_width_m, 4),
                "aspect": round(self.aspect, 3),
                "has_shadow": self.has_shadow,
                "quality": round(self.quality, 4)}


def object_height(shadow_len_m: float, object_range_m: float, altitude: float) -> float:
    """Similar-triangles seabed-object height from shadow length.

    h = H * Ls / (R + Ls), with H = sonar altitude, R = ground range to the object's far
    edge, Ls = shadow length. Returns 0 for a non-positive shadow.
    """
    if shadow_len_m <= 0:
        return 0.0
    denom = object_range_m + shadow_len_m
    if denom <= 0:
        return 0.0
    return altitude * shadow_len_m / denom


def extract(wf: Waterfall, bbox: tuple, highlight_thresh: Optional[float] = None,
            shadow_thresh: Optional[float] = None,
            max_shadow_px: int = 200) -> ShadowFeature:
    """Derive shadow geometry for a highlight whose bounding box is given.

    ``bbox`` is ``(i0, j0, i1, j1)`` (inclusive) around the highlight. The shadow is the
    run of low-intensity pixels immediately beyond the highlight in +range along the
    highlight's centre row. Thresholds default to mean +/- 1 sigma of the image.
    """
    i0, j0, i1, j1 = bbox
    mean, sd = _stats(wf)
    ht = highlight_thresh if highlight_thresh is not None else mean + sd
    st = shadow_thresh if shadow_thresh is not None else max(0.0, mean - sd)

    row_c = (j0 + j1) // 2
    row_c = min(max(row_c, 0), wf.rows - 1)

    # length (across-track extent) and width (along-track extent) of the highlight
    length_m = (i1 - i0 + 1) * wf.across_res
    width_m = (j1 - j0 + 1) * wf.along_res

    # walk the shadow behind the highlight along the centre row
    shadow_px = 0
    i = i1 + 1
    while i < wf.cols and shadow_px < max_shadow_px:
        if wf.pixels[row_c][i] <= st:
            shadow_px += 1
            i += 1
        else:
            break
    shadow_len_m = shadow_px * wf.across_res
    obj_range = wf.ground_range(i1)
    height = object_height(shadow_len_m, obj_range, wf.altitude)

    has_shadow = shadow_px >= 1
    # quality: prefers a clear, contiguous shadow of a few pixels; saturates ~10 px
    quality = 0.0
    if has_shadow:
        contrast = 1.0
        if sd > 0:
            contrast = min(1.0, (mean - st + 1e-9) / (sd + 1e-9) + 0.5)
        quality = min(1.0, shadow_px / 10.0) * max(0.2, contrast)

    aspect = (length_m / width_m) if width_m > 0 else 0.0
    return ShadowFeature(
        row_center=row_c, col_highlight_end=i1,
        shadow_len_px=shadow_px, shadow_len_m=shadow_len_m,
        object_range_m=obj_range, object_height_m=height,
        object_length_m=length_m, object_width_m=width_m,
        aspect=aspect, has_shadow=has_shadow, quality=round(quality, 4))


def grazing_angle(altitude: float, ground_range: float) -> float:
    """Grazing angle (degrees) at a ground range for a given sonar altitude."""
    if ground_range <= 0:
        return 90.0
    return math.degrees(math.atan2(altitude, ground_range))
