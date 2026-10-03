"""Small geometry helpers shared across the pipeline."""

from __future__ import annotations

import numpy as np

from scan.core.types import Intrinsics


def quat_to_rotmat(qx: float, qy: float, qz: float, qw: float) -> np.ndarray:
    """Rotation matrix from a unit quaternion (normalised defensively)."""
    q = np.array([qx, qy, qz, qw], dtype=np.float64)
    n = np.linalg.norm(q)
    if n == 0:
        raise ValueError("zero-length quaternion")
    x, y, z, w = q / n
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


def pose_matrix(translation: np.ndarray, rotation: np.ndarray) -> np.ndarray:
    T = np.eye(4)
    T[:3, :3] = rotation
    T[:3, 3] = translation
    return T


def rotation_angle_deg(R_a: np.ndarray, R_b: np.ndarray) -> float:
    """Angle of the relative rotation between two rotation matrices."""
    cos = (np.trace(R_a.T @ R_b) - 1.0) / 2.0
    return float(np.degrees(np.arccos(np.clip(cos, -1.0, 1.0))))


def backproject(
    depth: np.ndarray,
    intrinsics: Intrinsics,
    mask: np.ndarray | None = None,
    stride: int = 1,
) -> np.ndarray:
    """Depth map (metres, NaN invalid) -> Nx3 points in the OpenCV camera frame.

    ``intrinsics`` must match the depth map's resolution.
    """
    if depth.shape != (intrinsics.height, intrinsics.width):
        raise ValueError(
            f"depth shape {depth.shape} does not match intrinsics "
            f"{intrinsics.width}x{intrinsics.height}"
        )
    v, u = np.mgrid[0 : depth.shape[0] : stride, 0 : depth.shape[1] : stride]
    z = depth[::stride, ::stride]
    valid = np.isfinite(z) & (z > 0)
    if mask is not None:
        valid &= mask[::stride, ::stride]
    z = z[valid]
    x = (u[valid] - intrinsics.cx) * z / intrinsics.fx
    y = (v[valid] - intrinsics.cy) * z / intrinsics.fy
    return np.stack([x, y, z], axis=1)


def transform_points(T: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Apply a 4x4 rigid transform to Nx3 points."""
    return points @ T[:3, :3].T + T[:3, 3]
