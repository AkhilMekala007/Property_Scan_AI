from dataclasses import replace

import numpy as np
import pytest

from scan.core.types import CaptureMeta, Frame, FrameSet, Intrinsics, Tier, Trajectory
from scan.drift import DriftConfig, _constraint_ratio, _correct_trajectory, _yaw_deg, _yaw_only, correct_drift

K = Intrinsics(fx=60, fy=60, cx=47.5, cy=35.5, width=96, height=72)
LEVEL = np.diag([1.0, -1.0, -1.0])  # level camera looking along world -z


def yaw(deg):
    c, s = np.cos(np.radians(deg)), np.sin(np.radians(deg))
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def corner_room_depth(T: np.ndarray) -> np.ndarray:
    """Ray-cast depth of a box room (x in [-3, 3], z in [-3, 3], floor y=-1.4, ceiling y=1.2)."""
    v, u = np.mgrid[0:K.height, 0:K.width]
    rays_cam = np.stack([(u - K.cx) / K.fx, (v - K.cy) / K.fy, np.ones_like(u, float)], -1)
    R, o = T[:3, :3], T[:3, 3]
    d = rays_cam @ R.T
    t = np.full(d.shape[:2], np.inf)
    for axis, lo, hi in ((0, -3, 3), (1, -1.4, 1.2), (2, -3, 3)):
        with np.errstate(divide="ignore", invalid="ignore"):
            for plane in (lo, hi):
                tt = (plane - o[axis]) / d[..., axis]
                t = np.where((tt > 0) & (tt < t), tt, t)
    return t.astype(np.float32)  # z of rays_cam is 1, so t is the depth


def frame(i, position, R):
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = position
    depth = corner_room_depth(T)
    return Frame(i, float(i), K, T, depth_size=(K.width, K.height),
                 _depth=lambda d=depth: d, _confidence=lambda: np.full((K.height, K.width), 2, np.uint8))


def frameset(frames):
    meta = CaptureMeta("synthetic", Tier.LIDAR, "stray_scanner", source_dir=None)
    ts = np.array([f.timestamp for f in frames])
    traj = Trajectory(ts, np.array([f.T_world_cam[:3, 3] for f in frames]),
                      np.stack([f.T_world_cam[:3, :3] for f in frames]))
    return FrameSet(meta, frames, trajectory=traj)


def walk(n=80, drift_deg=0.0, drift_m=0.0):
    """Spin on the spot in the room; the second half carries an accumulated pose error."""
    frames = []
    for i in range(n):
        R = yaw(360.0 * i / (n // 2)) @ LEVEL
        p = np.array([0.0, 0.0, 0.0])
        T_true = np.eye(4)
        T_true[:3, :3], T_true[:3, 3] = R, p
        f = frame(i, p, R)  # depth rendered from the true pose
        if i >= n // 2:  # ...but the reported pose has drifted
            err = np.eye(4)
            err[:3, :3] = yaw(drift_deg)
            err[:3, 3] = [drift_m, 0, 0]
            f = replace(f, T_world_cam=err @ T_true)
        frames.append(f)
    return frames


def test_yaw_only_removes_roll_and_pitch():
    tilt = np.eye(4)
    tilt[:3, :3] = yaw(5) @ np.array([[1, 0, 0], [0, np.cos(0.1), -np.sin(0.1)], [0, np.sin(0.1), np.cos(0.1)]])
    out = _yaw_only(tilt)
    assert out[1, 1] == pytest.approx(1.0) and _yaw_deg(out) == pytest.approx(5.0, abs=0.3)


def test_constraint_ratio_flags_a_single_wall():
    info = np.zeros((6, 6))
    info[3, 3], info[5, 5] = 1000.0, 1.0  # x pinned, z free: sliding along a wall
    assert _constraint_ratio(info) < 0.01
    info[5, 5] = 800.0
    assert _constraint_ratio(info) > 0.5


def test_clean_poses_are_left_alone():
    fs, report = correct_drift(frameset(walk()), DriftConfig(fragment_size=10))
    assert report.max_translation_m < 0.01 and report.max_yaw_deg < 0.2


def test_drifted_second_lap_is_pulled_back():
    frames = walk(drift_deg=1.5, drift_m=0.08)
    fs, report = correct_drift(frameset(frames), DriftConfig(fragment_size=10))
    assert report.loops_accepted >= 1
    # after correction the second lap's poses sit much closer to the truth (identity position)
    err_before = np.linalg.norm(frames[-1].T_world_cam[:3, 3])
    err_after = np.linalg.norm(fs.frames[-1].T_world_cam[:3, 3])
    assert err_after < 0.5 * err_before
    assert fs.frames[0].T_world_cam[1, 1] == pytest.approx(frames[0].T_world_cam[1, 1])  # gravity kept


def test_short_capture_keeps_poses():
    fs0 = frameset(walk(n=12))
    fs, report = correct_drift(fs0, DriftConfig(fragment_size=10))
    assert fs is fs0 and "too short" in report.note


def test_trajectory_takes_fragment_correction():
    traj = Trajectory(np.arange(4.0), np.zeros((4, 3)), np.repeat(np.eye(3)[None], 4, 0))
    frags = [[Frame(0, 0.0, K)], [Frame(2, 2.0, K)]]
    shift = np.eye(4)
    shift[:3, 3] = [0.1, 0, 0]
    out = _correct_trajectory(traj, frags, [np.eye(4), shift])
    assert np.allclose(out.positions[:2, 0], 0) and np.allclose(out.positions[2:, 0], 0.1)
