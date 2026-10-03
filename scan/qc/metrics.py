"""Per-frame quality measurements. No decisions here; see ``scan.qc.decide``."""

from __future__ import annotations

import cv2
import numpy as np

from scan.core.types import Frame, Trajectory
from scan.qc.report import FrameQuality

SHARPNESS_WIDTH = 640  # sharpness is measured at a fixed width so captures are comparable
SPEED_WINDOW_S = 0.1  # speed is a central difference over this window


def image_stats(rgb: np.ndarray) -> tuple[float, float, float, float]:
    """(sharpness, mean brightness, near-black share, blown-out share)."""
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    scale = SHARPNESS_WIDTH / gray.shape[1]
    if scale < 1:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return (
        sharpness,
        float(gray.mean()),
        float((gray < 16).mean()),
        float((gray > 245).mean()),
    )


def pitch_deg(T_world_cam: np.ndarray) -> float:
    """Elevation of the optical axis above the horizon (world +y is up)."""
    forward = T_world_cam[:3, 2]  # camera +z in world
    return float(np.degrees(np.arcsin(np.clip(forward[1], -1.0, 1.0))))


def motion_at(traj: Trajectory, timestamp: float) -> tuple[float, float]:
    """(linear m/s, angular deg/s) around ``timestamp`` from the full-rate path."""
    t = traj.timestamps
    lo = int(np.searchsorted(t, timestamp - SPEED_WINDOW_S / 2))
    hi = int(np.searchsorted(t, timestamp + SPEED_WINDOW_S / 2))
    lo = max(0, min(lo, len(t) - 2))
    hi = max(lo + 1, min(hi, len(t) - 1))
    dt = t[hi] - t[lo]
    if dt <= 0:
        return 0.0, 0.0
    speed = float(np.linalg.norm(traj.positions[hi] - traj.positions[lo]) / dt)
    R = traj.rotations[lo].T @ traj.rotations[hi]
    angle = float(np.degrees(np.arccos(np.clip((np.trace(R) - 1) / 2, -1.0, 1.0))))
    return speed, angle / dt


def tracking_jumps(traj: Trajectory, jump_speed_mps: float, merge_s: float = 0.5) -> list[float]:
    """Timestamps where consecutive poses imply impossible speed (tracking relocalised).

    Steps closer together than ``merge_s`` are one event, reported at its first step.
    """
    if len(traj) < 2:
        return []
    dt = np.diff(traj.timestamps)
    step = np.linalg.norm(np.diff(traj.positions, axis=0), axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        speed = np.where(dt > 0, step / dt, 0.0)
    events: list[float] = []
    for i in np.flatnonzero(speed > jump_speed_mps):
        t = float(traj.timestamps[i + 1])
        if not events or t - events[-1] > merge_s:
            events.append(t)
    return events


def measure_frame(frame: Frame, traj: Trajectory | None) -> FrameQuality:
    sharpness, brightness, dark, bright = image_stats(frame.rgb())
    q = FrameQuality(
        index=frame.index,
        timestamp=frame.timestamp,
        sharpness=sharpness,
        brightness=brightness,
        dark_frac=dark,
        bright_frac=bright,
    )
    depth = frame.depth()
    if depth is not None:
        q.depth_valid_frac = float(np.isfinite(depth).mean())
        conf = frame.confidence()
        if conf is not None:
            q.high_conf_frac = float((conf == 2).mean())
    if frame.T_world_cam is not None:
        q.pitch_deg = pitch_deg(frame.T_world_cam)
    if traj is not None:
        q.speed_mps, q.angular_dps = motion_at(traj, frame.timestamp)
    return q
