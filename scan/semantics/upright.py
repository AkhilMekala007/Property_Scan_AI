"""Rotate images so the world's 'up' points to the top of the image, and back.

Segmentation models are trained on upright photos. Phones are often held in
portrait while the video is stored landscape, so frames arrive sideways.
"""

from __future__ import annotations

import numpy as np


def upright_quarter_turns(T_world_cam: np.ndarray | None) -> int:
    """Number of 90-degree counter-clockwise turns (np.rot90 ``k``) that make the image upright.

    The world up vector expressed in the OpenCV camera frame tells us where 'up' lies
    in the image plane; image-up is camera -y.
    """
    if T_world_cam is None:
        return 0
    up_cam = T_world_cam[:3, :3].T @ np.array([0.0, 1.0, 0.0])
    ux, uy = up_cam[0], up_cam[1]
    if abs(uy) >= abs(ux):
        return 0 if uy < 0 else 2  # up is image-top, or the image is upside down
    return 1 if ux > 0 else 3  # up is image-right -> turn CCW; image-left -> turn CW


def to_upright(image: np.ndarray, k: int) -> np.ndarray:
    return np.ascontiguousarray(np.rot90(image, k))


def from_upright(image: np.ndarray, k: int) -> np.ndarray:
    return np.ascontiguousarray(np.rot90(image, -k))
