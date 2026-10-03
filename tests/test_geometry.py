import numpy as np
import pytest

from scan.core.geometry import (
    backproject,
    pose_matrix,
    quat_to_rotmat,
    rotation_angle_deg,
    transform_points,
)
from scan.core.types import Intrinsics


def test_identity_quaternion():
    assert np.allclose(quat_to_rotmat(0, 0, 0, 1), np.eye(3))


def test_quaternion_90deg_about_y():
    s = np.sqrt(0.5)
    R = quat_to_rotmat(0, s, 0, s)
    assert np.allclose(R @ [0, 0, 1], [1, 0, 0])
    assert rotation_angle_deg(np.eye(3), R) == pytest.approx(90.0)


def test_unnormalised_quaternion_is_normalised():
    assert np.allclose(quat_to_rotmat(0, 0, 0, 2), np.eye(3))


def test_intrinsics_scaling_keeps_pixel_centres():
    k = Intrinsics(fx=1600, fy=1600, cx=959.5, cy=719.5, width=1920, height=1440)
    small = k.scaled_to(256, 192)
    assert small.fx == pytest.approx(1600 * 256 / 1920)
    # exact image centre stays the exact image centre
    assert small.cx == pytest.approx(127.5)
    assert small.cy == pytest.approx(95.5)


def test_backproject_principal_point_and_nan():
    k = Intrinsics(fx=10, fy=10, cx=1, cy=1, width=3, height=3)
    depth = np.full((3, 3), 2.0, np.float32)
    depth[0, 0] = np.nan
    pts = backproject(depth, k)
    assert len(pts) == 8
    assert any(np.allclose(p, [0, 0, 2]) for p in pts)  # centre pixel straight ahead
    assert any(np.allclose(p, [0.2, 0.2, 2]) for p in pts)  # bottom-right: +x right, +y down


def test_backproject_rejects_mismatched_intrinsics():
    k = Intrinsics(fx=10, fy=10, cx=1, cy=1, width=4, height=3)
    with pytest.raises(ValueError, match="does not match"):
        backproject(np.ones((3, 3), np.float32), k)


def test_transform_points():
    T = pose_matrix(np.array([1.0, 2.0, 3.0]), quat_to_rotmat(0, 0, 0, 1))
    assert np.allclose(transform_points(T, np.zeros((1, 3))), [[1, 2, 3]])
