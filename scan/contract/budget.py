"""Provisional error budget: fit sigma + systematic terms -> 90 % intervals.

Fit sigmas from plane fits are tiny (they exclude depth bias, drift, labelling and
segmentation errors). Until C12 fits these terms on the tape-measured benchmark, each
quantity gets a conservative systematic term per tier. Every interval built here is marked
``calibrated: false`` in the output.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Z90 = 1.6449  # two-sided 90 %


@dataclass(frozen=True)
class TierBudget:
    wall_abs_m: float  # per wall length, absolute part
    wall_rel: float  # per wall length, proportional part
    surface_offset_m: float  # per wall face position (drives area)
    ceiling_m: float
    opening_m: float
    opening_height_m: float
    damage_rel: float  # damage area, proportional
    damage_length_m: float


BUDGETS = {
    "lidar": TierBudget(0.010, 0.003, 0.007, 0.010, 0.015, 0.020, 0.15, 0.05),
    # placeholders until the video / photo tiers exist; the gates allow +-3 % and +-8 %
    "video": TierBudget(0.03, 0.015, 0.03, 0.04, 0.04, 0.05, 0.25, 0.10),
    "photo": TierBudget(0.06, 0.04, 0.06, 0.08, 0.08, 0.10, 0.35, 0.15),
}

INTERVAL_METHOD = ("provisional error budget: fit sigma combined with per-tier systematic terms "
                   "(uncalibrated; replaced by benchmark-fitted factors in C12)")


def combine(*sigmas: float) -> float:
    return math.sqrt(sum(s * s for s in sigmas if s is not None))


def budget_for(tier: str) -> TierBudget:
    return BUDGETS.get(tier, BUDGETS["photo"])
