"""Top-down sanity render of a posed FrameSet: wall points + camera path.

Debug aid only. If walls come out sharp and straight, intrinsics, depth units
and pose conventions are all consistent.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from scan.core.geometry import backproject, transform_points
from scan.core.types import FrameSet

RES_M = 0.02  # metres per pixel


def world_points(frameset: FrameSet, max_frames: int = 150, stride: int = 2) -> np.ndarray:
    step = max(1, len(frameset.frames) // max_frames)
    clouds = []
    for frame in frameset.frames[::step]:
        depth = frame.depth()
        if depth is None or frame.T_world_cam is None:
            continue
        conf = frame.confidence()
        mask = conf == 2 if conf is not None else None
        pts = backproject(depth, frame.depth_intrinsics, mask=mask, stride=stride)
        clouds.append(transform_points(frame.T_world_cam, pts))
    if not clouds:
        raise ValueError("no posed depth frames to render")
    return np.concatenate(clouds)


def floor_height(points: np.ndarray) -> float:
    """World y of the floor: the densest horizontal slab below the median point height."""
    y = points[:, 1]
    below = y[y < np.median(y)]
    hist, edges = np.histogram(below, bins=np.arange(below.min(), below.max() + 0.02, 0.02))
    k = int(np.argmax(hist))
    return float((edges[k] + edges[k + 1]) / 2)


def render_topdown(frameset: FrameSet, out_path: Path) -> dict:
    pts = world_points(frameset)
    floor_y = floor_height(pts)
    band = pts[(pts[:, 1] > floor_y + 0.3) & (pts[:, 1] < floor_y + 1.9)]
    xs, zs = band[:, 0], band[:, 2]
    x0, x1 = np.percentile(xs, [0.5, 99.5])
    z0, z1 = np.percentile(zs, [0.5, 99.5])
    w = int((x1 - x0) / RES_M) + 1
    h = int((z1 - z0) / RES_M) + 1

    density = np.zeros((h, w), np.float32)
    ix = ((xs - x0) / RES_M).astype(int)
    iz = ((zs - z0) / RES_M).astype(int)
    keep = (ix >= 0) & (ix < w) & (iz >= 0) & (iz < h)
    np.add.at(density, (iz[keep], ix[keep]), 1)
    nonzero = density[density > 0]
    density = np.clip(density / np.percentile(nonzero, 95), 0, 1) if nonzero.size else density
    image = cv2.cvtColor((255 - density * 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)

    cam = np.array([f.T_world_cam[:3, 3] for f in frameset.frames])
    tx = ((cam[:, 0] - x0) / RES_M).astype(int)
    tz = ((cam[:, 2] - z0) / RES_M).astype(int)
    for a in range(1, len(tx)):
        cv2.line(image, (tx[a - 1], tz[a - 1]), (tx[a], tz[a]), (0, 0, 255), 1)
    cv2.circle(image, (tx[0], tz[0]), 6, (0, 170, 0), -1)
    cv2.circle(image, (tx[-1], tz[-1]), 6, (255, 0, 0), -1)
    for metre in range(int(x1 - x0) + 1):  # 1 m ticks along the top edge
        cv2.line(image, (int(metre / RES_M), 0), (int(metre / RES_M), 8), (0, 0, 0), 2)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), image)
    return {
        "floor_y": floor_y,
        "camera_height_m": float(np.median(cam[:, 1]) - floor_y),
        "extent_m": (float(x1 - x0), float(z1 - z0)),
        "n_points": int(len(pts)),
    }
