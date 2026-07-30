"""sonar — side-scan contact detector + mine-like-object (MLO) ATR pipeline.

Route-survey and mine-countermeasure work in support of amphibious/riverine logistics
depends on turning side-scan/SAS waterfall imagery into ranked *contacts*. Because labelled
sonar data is famously scarce, this pipeline deliberately pairs a lightweight learned
score with **physics-grounded, explainable features** — the highlight-plus-acoustic-shadow
geometry from :mod:`shadowgeo` — so a human analyst always has an auditable rationale for
why a contact was flagged.

Pipeline stages::

    waterfall --> detect highlights (threshold + connected components)
              --> derive highlight/shadow geometry (height, length, aspect)
              --> classify MLO vs clutter (engineered features + a small learned score)
              --> emit standardised ContactRecords, ranked by confidence

This is detection/survey for hazard avoidance and route clearance. It classifies and
locates seabed contacts; it does not fuze, neutralise, aim, or engage anything. Pure
stdlib, deterministic, offline.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

from conflictwatch.autonomy.shadowgeo import Waterfall, extract, _stats, grazing_angle

CLASSES = ("mine_like", "clutter")

# plausible physical bounds for a mine-like object (metres)
MLO_SIZE = {"min_len": 0.3, "max_len": 3.5, "min_h": 0.1, "max_h": 2.0}


@dataclass
class ContactRecord:
    """A standardised sonar contact."""
    id: int
    along_track_m: float
    range_m: float
    length_m: float
    width_m: float
    height_m: float
    shadow_len_m: float
    aspect: float
    grazing_deg: float
    cls: str
    score: float
    engineered: float
    learned: float

    def as_dict(self) -> dict:
        return {"id": self.id,
                "along_track_m": round(self.along_track_m, 3),
                "range_m": round(self.range_m, 3),
                "length_m": round(self.length_m, 4),
                "width_m": round(self.width_m, 4),
                "height_m": round(self.height_m, 4),
                "shadow_len_m": round(self.shadow_len_m, 4),
                "aspect": round(self.aspect, 3),
                "grazing_deg": round(self.grazing_deg, 2),
                "class": self.cls, "score": round(self.score, 4),
                "engineered": round(self.engineered, 4),
                "learned": round(self.learned, 4)}


def _sigmoid(x: float) -> float:
    if x < -60:
        return 0.0
    if x > 60:
        return 1.0
    return 1.0 / (1.0 + math.exp(-x))


def _find_highlights(wf: Waterfall, thresh: float, min_pixels: int) -> list:
    """4-connected components of pixels above ``thresh``; returns bounding boxes."""
    seen = [[False] * wf.cols for _ in range(wf.rows)]
    boxes = []
    for sj in range(wf.rows):
        for si in range(wf.cols):
            if seen[sj][si] or wf.pixels[sj][si] <= thresh:
                continue
            # flood fill
            stack = [(si, sj)]
            seen[sj][si] = True
            comp = []
            while stack:
                i, j = stack.pop()
                comp.append((i, j))
                for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ni, nj = i + di, j + dj
                    if (0 <= ni < wf.cols and 0 <= nj < wf.rows
                            and not seen[nj][ni] and wf.pixels[nj][ni] > thresh):
                        seen[nj][ni] = True
                        stack.append((ni, nj))
            if len(comp) >= min_pixels:
                i0 = min(c[0] for c in comp)
                i1 = max(c[0] for c in comp)
                j0 = min(c[1] for c in comp)
                j1 = max(c[1] for c in comp)
                boxes.append((i0, j0, i1, j1, len(comp)))
    # deterministic order: top-to-bottom, left-to-right
    boxes.sort(key=lambda b: (b[1], b[0]))
    return boxes


def _engineered_score(feat, length_m: float) -> float:
    """Physics-grounded MLO likelihood from size/shadow/height features (0..1)."""
    s = MLO_SIZE
    # length within plausible band -> 1, outside -> decays
    if s["min_len"] <= length_m <= s["max_len"]:
        size_score = 1.0
    else:
        d = (s["min_len"] - length_m) if length_m < s["min_len"] else (length_m - s["max_len"])
        size_score = max(0.0, 1.0 - d / 2.0)
    # a real MLO casts a shadow; height should be plausible
    h = feat.object_height_m
    if feat.has_shadow and s["min_h"] <= h <= s["max_h"]:
        shadow_score = 0.5 + 0.5 * feat.quality
    elif feat.has_shadow:
        shadow_score = 0.3 * feat.quality
    else:
        shadow_score = 0.05
    # aspect near 1..4 typical for cylindrical/rounded MLOs
    a = feat.aspect
    aspect_score = 1.0 if 0.5 <= a <= 4.0 else max(0.0, 1.0 - abs(a - 2.0) / 6.0)
    return max(0.0, min(1.0, 0.45 * size_score + 0.4 * shadow_score + 0.15 * aspect_score))


def _learned_score(feat, length_m: float, altitude: float) -> float:
    """A small fixed-weight logistic standing in for a CNN/transformer head.

    Deterministic and transparent — weights chosen so the learned channel broadly agrees
    with the physics on clear cases while adding independent signal on ambiguous ones.
    """
    # normalised features
    x_len = (length_m - 1.5) / 1.5
    x_h = (feat.object_height_m - 0.5) / 0.5
    x_shadow = 1.0 if feat.has_shadow else -1.0
    x_qual = (feat.quality - 0.5) * 2.0
    z = (1.8 * x_shadow + 1.1 * x_qual
         - 0.6 * abs(x_len) - 0.5 * abs(x_h) + 0.2)
    return _sigmoid(z)


def detect(wf: Waterfall, highlight_thresh: Optional[float] = None,
           min_pixels: int = 3, threshold: float = 0.5,
           weight_engineered: float = 0.5) -> list:
    """Run the full detect -> geometry -> classify pipeline over a waterfall.

    Returns a list of `ContactRecord`, ranked by confidence (descending). ``threshold`` is
    the score at/above which a contact is labelled ``mine_like``. ``weight_engineered``
    blends the explainable and learned channels (0 = learned only, 1 = engineered only).
    """
    mean, sd = _stats(wf)
    ht = highlight_thresh if highlight_thresh is not None else mean + sd
    we = 0.0 if weight_engineered < 0 else 1.0 if weight_engineered > 1 else float(weight_engineered)

    contacts = []
    for cid, (i0, j0, i1, j1, npx) in enumerate(_find_highlights(wf, ht, min_pixels)):
        feat = extract(wf, (i0, j0, i1, j1), highlight_thresh=ht)
        length_m = feat.object_length_m
        eng = _engineered_score(feat, length_m)
        lrn = _learned_score(feat, length_m, wf.altitude)
        score = we * eng + (1.0 - we) * lrn
        cls = "mine_like" if score >= threshold else "clutter"
        rng = feat.object_range_m
        contacts.append(ContactRecord(
            id=cid,
            along_track_m=((j0 + j1) / 2.0) * wf.along_res,
            range_m=rng, length_m=length_m, width_m=feat.object_width_m,
            height_m=feat.object_height_m, shadow_len_m=feat.shadow_len_m,
            aspect=feat.aspect, grazing_deg=grazing_angle(wf.altitude, rng),
            cls=cls, score=score, engineered=eng, learned=lrn))
    contacts.sort(key=lambda c: (-c.score, c.along_track_m, c.range_m))
    return contacts


def summarize(contacts: Sequence[ContactRecord]) -> dict:
    """Roll up a contact list for an analyst hand-off."""
    mlo = [c for c in contacts if c.cls == "mine_like"]
    return {
        "contacts": len(contacts),
        "mine_like": len(mlo),
        "clutter": len(contacts) - len(mlo),
        "top": mlo[0].as_dict() if mlo else None,
        "records": [c.as_dict() for c in contacts],
    }
