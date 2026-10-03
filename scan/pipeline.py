"""The LiDAR-tier pipeline as one function, so every command runs the same chain.

ingest -> C3 QC -> C5 drift correction -> C4 semantics -> C7a structure
       -> C6 rooms -> C7b measurement -> C8 openings -> C9 stitching
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from scan.core.types import FrameSet

STAGES = ("qc", "drift", "semantics", "structure", "rooms", "measure", "openings", "plan")


@dataclass
class PipelineResult:
    frameset: FrameSet  # as loaded
    qc: object = None
    drift: object = None  # DriftReport
    labelled: FrameSet | None = None  # QC'd, drift-corrected, labelled frames
    model: object = None  # StructureModel
    layout: object = None  # RoomLayout
    rooms: list = field(default_factory=list)
    openings: list = field(default_factory=list)
    plan: object = None  # PropertyPlan


def run_pipeline(frameset: FrameSet, upto: str = "plan", drift: bool = True) -> PipelineResult:
    from scan.drift import DriftReport, correct_drift
    from scan.qc import run_qc

    stop = STAGES.index(upto)
    res = PipelineResult(frameset)
    res.qc = run_qc(frameset)
    frames = res.qc.frameset
    if stop >= STAGES.index("drift"):
        if drift:
            frames, res.drift = correct_drift(frames)
        else:
            res.drift = DriftReport(False, note="drift correction disabled (--no-drift-fix)")
    if stop >= STAGES.index("semantics"):
        from scan.semantics import run_semantics

        frames = run_semantics(frames).frameset
    res.labelled = frames
    if stop >= STAGES.index("structure"):
        from scan.structure import run_structure

        cov = res.qc.report.coverage
        res.model = run_structure(frames, floor_hint_y=cov.floor_y, ceiling_seen=cov.ceiling_seen)
    if stop >= STAGES.index("rooms"):
        from scan.rooms import segment_rooms

        res.layout = segment_rooms(res.model, frames.trajectory)
    if stop >= STAGES.index("measure"):
        from scan.measure import measure_rooms

        res.rooms, _ = measure_rooms(res.model, res.layout)
    if stop >= STAGES.index("openings"):
        from scan.openings import find_openings

        res.openings = find_openings(res.model, res.layout, res.rooms, frames)
    if stop >= STAGES.index("plan"):
        from scan.stitch import stitch

        res.plan = stitch(res.rooms, res.openings, res.layout)
    return res


def consistency_metrics(res: PipelineResult) -> dict:
    """Self-consistency of the reconstruction, used for the drift ablation (no ground truth needed).

    Drift makes surfaces seen at different times disagree: walls fit with more spread, one wall
    splits into near-duplicate planes, floors get thicker.
    """
    m = res.model
    walls = m.walls
    area = np.array([w.area_seen_m2 for w in walls]) if walls else np.zeros(0)
    rms = np.array([w.rms_m for w in walls]) if walls else np.zeros(0)
    big = area >= 1.0
    out = {
        "walls": len(walls),
        "wall_fit_spread_mm_area_weighted": round(float(np.average(rms, weights=area) * 1000), 2) if len(walls) else None,
        "wall_fit_spread_mm_big_walls_median": round(float(np.median(rms[big]) * 1000), 2) if big.any() else None,
        "wall_voxels_explained": round(m.stats.n_wall_voxels_assigned / max(m.stats.n_wall_voxels, 1), 3),
        "floor_fit_spread_mm": round(m.floor.rms_m * 1000, 2) if m.floor else None,
    }
    if res.plan is not None:
        p = res.plan
        out.update({
            "rooms": len(p.rooms),
            "net_area_m2": p.net_area_m2,
            "footprint_m2": p.footprint_m2,
            "shared_walls": len(p.shared_walls),
            "overlap_m2": round(sum(a for _, _, a in p.overlaps), 3),
            "connected": p.connected_components == 1,
        })
    return out
