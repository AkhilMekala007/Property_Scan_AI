"""Label self-check against geometry: no hand labels needed.

Floor pixels should be horizontal surfaces below the camera, ceiling pixels
horizontal surfaces above it, wall pixels vertical surfaces. The share of
labelled pixels that agree is a per-capture accuracy signal for the segmenter.
"""

from __future__ import annotations

import cv2
import numpy as np

from scan.core.geometry import organised_points_normals
from scan.core.types import Frame
from scan.semantics.classes import Surface

MAX_FRAMES = 40
STRIDE = 2
HORIZONTAL = 0.9  # |n . up| above this
VERTICAL = 0.3  # |n . up| below this


def labels_at(frame: Frame, size: tuple[int, int]) -> np.ndarray | None:
    """Label map resized (nearest) to ``size`` = (width, height), e.g. the depth resolution."""
    labels = frame.labels()
    if labels is None:
        return None
    if (labels.shape[1], labels.shape[0]) == size:
        return labels
    return cv2.resize(labels, size, interpolation=cv2.INTER_NEAREST)


def geometry_agreement(frames: list[Frame]) -> dict[str, dict[str, float]]:
    """{class: {"agreement": share, "pixels": n}} for wall, floor and ceiling."""
    usable = [f for f in frames if f.has_labels and f.has_depth and f.T_world_cam is not None]
    step = max(1, len(usable) // MAX_FRAMES)
    hits = {s: 0 for s in (Surface.WALL, Surface.FLOOR, Surface.CEILING)}
    totals = dict(hits)
    for frame in usable[::step]:
        depth = frame.depth()
        conf = frame.confidence()
        P, N = organised_points_normals(
            depth, frame.depth_intrinsics, frame.T_world_cam, stride=STRIDE,
            valid=conf == 2 if conf is not None else None,
        )
        labels = labels_at(frame, frame.depth_size)[::STRIDE, ::STRIDE][:-1, :-1]
        ok = np.all(np.isfinite(P), axis=-1) & np.all(np.isfinite(N), axis=-1)
        ny = np.abs(N[..., 1])
        cam_y = frame.T_world_cam[1, 3]
        rules = {
            Surface.WALL: ny < VERTICAL,
            Surface.FLOOR: (ny > HORIZONTAL) & (P[..., 1] < cam_y - 0.5),
            Surface.CEILING: (ny > HORIZONTAL) & (P[..., 1] > cam_y + 0.3),
        }
        for surface, agrees in rules.items():
            mask = ok & (labels == surface)
            totals[surface] += int(mask.sum())
            hits[surface] += int((mask & agrees).sum())
    return {
        s.name.lower(): {
            "agreement": round(hits[s] / totals[s], 3) if totals[s] else None,
            "pixels": totals[s],
        }
        for s in hits
    }
