"""C3 QC: measure every keyframe, drop unusable ones, report what went wrong."""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from scan.core.types import FrameSet, Tier
from scan.qc.coverage import measure_coverage
from scan.qc.decide import find_issues, mark_frames, quality_score
from scan.qc.metrics import measure_frame, tracking_jumps
from scan.qc.report import QcConfig, QualityReport, write_reports

__all__ = ["QcConfig", "QcResult", "QualityReport", "run_qc", "write_reports"]


@dataclass
class QcResult:
    frameset: FrameSet  # kept frames only
    report: QualityReport


def run_qc(frameset: FrameSet, config: QcConfig | None = None) -> QcResult:
    cfg = config or QcConfig()
    traj = frameset.trajectory
    jumps = tracking_jumps(traj, cfg.jump_speed_mps) if traj is not None else []

    qualities = [measure_frame(f, traj) for f in frameset.frames]
    mark_frames(qualities, jumps, cfg, depth_from_sensor=frameset.meta.tier is Tier.LIDAR)
    kept_frames = [
        replace(f, rgb_ok=q.rgb_ok) for f, q in zip(frameset.frames, qualities) if q.kept
    ]

    coverage = measure_coverage(kept_frames, cfg)
    median_brightness = float(np.median([q.brightness for q in qualities])) if qualities else 0.0
    issues = find_issues(frameset.meta, qualities, coverage, jumps, median_brightness, cfg)

    report = QualityReport(
        capture_id=frameset.meta.capture_id,
        tier=frameset.meta.tier.value,
        n_frames_in=len(qualities),
        n_frames_kept=len(kept_frames),
        quality_score=quality_score(qualities, issues),
        median_brightness=median_brightness,
        coverage=coverage,
        issues=issues,
        frames=qualities,
        tracking_jumps_s=jumps,
    )
    return QcResult(frameset=frameset.with_frames(kept_frames), report=report)
