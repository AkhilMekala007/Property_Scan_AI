"""Capture-level coverage: were the floor and the ceiling actually observed?

Uses horizontal surfaces only (from depth-map normals), so walls and the sides
of furniture never count as floor or ceiling.
"""

from __future__ import annotations

import numpy as np

from scan.core.types import Frame
from scan.qc.report import Coverage, QcConfig

MAX_FRAMES = 80
DEPTH_STRIDE = 2
HORIZONTAL_COS = 0.9  # |normal . up| above this = horizontal surface (within ~25 deg)
SLAB_M = 0.05  # half-thickness of the floor / ceiling slab around its mode height
CELL_M = 0.10  # area is counted on a 10 cm grid
UP_PITCH_DEG = 30.0


def _organised_points(frame: Frame) -> tuple[np.ndarray, np.ndarray] | None:
    """World points and world normals on an organised grid, horizontal-surface pixels only."""
    depth = frame.depth()
    if depth is None or frame.T_world_cam is None:
        return None
    K = frame.depth_intrinsics
    v, u = np.mgrid[0 : depth.shape[0] : DEPTH_STRIDE, 0 : depth.shape[1] : DEPTH_STRIDE]
    z = depth[::DEPTH_STRIDE, ::DEPTH_STRIDE].astype(np.float64)
    conf = frame.confidence()
    if conf is not None:
        z = np.where(conf[::DEPTH_STRIDE, ::DEPTH_STRIDE] == 2, z, np.nan)
    P = np.stack([(u - K.cx) * z / K.fx, (v - K.cy) * z / K.fy, z], axis=-1)

    dx = P[:-1, 1:] - P[:-1, :-1]
    dy = P[1:, :-1] - P[:-1, :-1]
    n = np.cross(dx, dy)
    norm = np.linalg.norm(n, axis=-1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        n = n / norm
    pts = P[:-1, :-1]
    R, t = frame.T_world_cam[:3, :3], frame.T_world_cam[:3, 3]
    n_world = n @ R.T
    p_world = pts @ R.T + t
    ok = np.all(np.isfinite(p_world), axis=-1) & np.all(np.isfinite(n_world), axis=-1)
    horizontal = ok & (np.abs(n_world[..., 1]) > HORIZONTAL_COS)
    return p_world[horizontal], n_world[horizontal]


def _mode_height(y: np.ndarray) -> float:
    bins = np.arange(y.min(), y.max() + 0.02, 0.02)
    if len(bins) < 2:
        return float(y.mean())
    hist, edges = np.histogram(y, bins=bins)
    k = int(np.argmax(hist))
    return float((edges[k] + edges[k + 1]) / 2)


def _slab_area(points: np.ndarray, height: float) -> float:
    slab = points[np.abs(points[:, 1] - height) < SLAB_M]
    if len(slab) == 0:
        return 0.0
    cells = np.unique(np.floor(slab[:, [0, 2]] / CELL_M).astype(np.int64), axis=0)
    return float(len(cells) * CELL_M * CELL_M)


def measure_coverage(frames: list[Frame], cfg: QcConfig) -> Coverage:
    posed = [f for f in frames if f.T_world_cam is not None]
    if not posed:
        return Coverage()  # photo tier: decided later from estimated geometry

    cov = Coverage()
    pitches = np.array([np.degrees(np.arcsin(np.clip(f.T_world_cam[1, 2], -1, 1))) for f in posed])
    cov.up_frames_frac = float((pitches > UP_PITCH_DEG).mean())
    cam_y = float(np.median([f.T_world_cam[1, 3] for f in posed]))

    step = max(1, len(posed) // MAX_FRAMES)
    clouds = [c for f in posed[::step] if (c := _organised_points(f)) is not None]
    if not clouds:
        return cov
    pts = np.concatenate([c[0] for c in clouds])

    below = pts[pts[:, 1] < cam_y - 0.5]
    if len(below):
        floor_y = _mode_height(below[:, 1])
        cov.floor_y = floor_y
        cov.floor_area_m2 = _slab_area(below, floor_y)
        cov.floor_seen = cov.floor_area_m2 >= cfg.min_surface_area_m2
        cov.camera_height_m = cam_y - floor_y
    else:
        cov.floor_seen, cov.floor_area_m2 = False, 0.0

    ceiling_floor = (
        cov.floor_y + cfg.ceiling_min_above_floor_m if cov.floor_seen else cam_y + 0.6
    )
    above = pts[pts[:, 1] > ceiling_floor]
    if len(above):
        ceiling_y = _mode_height(above[:, 1])
        cov.ceiling_area_m2 = _slab_area(above, ceiling_y)
        cov.ceiling_seen = cov.ceiling_area_m2 >= cfg.min_surface_area_m2
    else:
        cov.ceiling_seen, cov.ceiling_area_m2 = False, 0.0
    return cov
