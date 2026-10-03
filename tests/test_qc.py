import json

import cv2
import numpy as np
import pytest

from scan.core.devices import lookup_device
from scan.core.geometry import pose_matrix
from scan.core.types import CaptureMeta, Frame, FrameSet, Intrinsics, Tier, Trajectory
from scan.ingest import load_capture
from scan.qc import QcConfig, run_qc, write_reports
from scan.qc.coverage import measure_coverage
from scan.qc.decide import find_issues, mark_frames, relative_sharpness
from scan.qc.metrics import image_stats, motion_at, tracking_jumps
from scan.qc.report import Coverage, FrameQuality

CFG = QcConfig()
K_DEPTH = Intrinsics(fx=40, fy=40, cx=31.5, cy=23.5, width=64, height=48)
LOOK_UP = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], float)  # camera +z -> world +y
LOOK_DOWN = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], float)  # camera +z -> world -y


def checkerboard(size=64, square=8):
    tile = (np.indices((size, size)).sum(axis=0) // square) % 2
    img = (tile * 255).astype(np.uint8)
    return np.dstack([img] * 3)


def flat_frame(index, rotation, distance_m, x=0.0):
    depth = np.full((48, 64), distance_m, np.float32)
    return Frame(
        index=index,
        timestamp=float(index),
        intrinsics=K_DEPTH,
        T_world_cam=pose_matrix(np.array([x, 0.0, 0.0]), rotation),
        depth_size=(64, 48),
        _rgb=lambda: checkerboard(),
        _depth=lambda d=depth: d,
        _confidence=lambda: np.full((48, 64), 2, np.uint8),
    )


def quality(i, **kw):
    base = dict(index=i, timestamp=float(i), sharpness=100.0, brightness=140.0)
    base.update(kw)
    return FrameQuality(**base)


# ---------- metrics ----------

def test_blur_lowers_sharpness():
    sharp = image_stats(checkerboard())[0]
    blurred = image_stats(cv2.GaussianBlur(checkerboard(), (9, 9), 3))[0]
    assert blurred < 0.5 * sharp


def test_dark_and_bright_fractions():
    _, mean, dark, bright = image_stats(np.zeros((32, 32, 3), np.uint8))
    assert mean == 0 and dark == 1.0 and bright == 0.0


def test_relative_sharpness_flags_a_dip_not_a_plain_wall():
    # a long stretch of low-texture frames is normal; one dip among sharp neighbours is blur
    s = [100, 100, 100, 20, 100, 100, 100, 30, 30, 30, 30, 30, 30]
    rel = relative_sharpness(s, window=2)
    assert rel[3] < 0.4
    assert all(r > 0.9 for r in rel[8:])


def test_motion_from_trajectory():
    t = np.arange(0, 2, 0.02)
    traj = Trajectory(t, np.stack([t * 0.5, 0 * t, 0 * t], 1), np.repeat(np.eye(3)[None], len(t), 0))
    speed, ang = motion_at(traj, 1.0)
    assert speed == pytest.approx(0.5, rel=0.05)
    assert ang == pytest.approx(0.0, abs=1e-6)


def test_tracking_jumps_are_merged_into_one_event():
    t = np.arange(0, 1, 0.02)
    pos = np.zeros((len(t), 3))
    pos[25:, 0] = 1.0  # 1 m in one 20 ms step = 50 m/s
    pos[26:, 0] = 1.2  # a second fast step right after
    traj = Trajectory(t, pos, np.repeat(np.eye(3)[None], len(t), 0))
    jumps = tracking_jumps(traj, 3.0)
    assert len(jumps) == 1 and jumps[0] == pytest.approx(t[25])


# ---------- per-frame decisions ----------

def test_blurry_frame_kept_for_lidar_but_dropped_otherwise():
    for sensor_depth, expect_kept in ((True, True), (False, False)):
        frames = [quality(i) for i in range(40)]
        frames[20].sharpness = 10.0
        mark_frames(frames, [], CFG, depth_from_sensor=sensor_depth)
        assert frames[20].rgb_ok is False
        assert frames[20].kept is expect_kept


@pytest.mark.parametrize(
    "field, value, reason",
    [
        ("speed_mps", 2.5, "fast_motion"),
        ("angular_dps", 200.0, "fast_motion"),
        ("dark_frac", 0.9, "too_dark"),
        ("bright_frac", 0.9, "blown_out"),
        ("depth_valid_frac", 0.1, "low_depth"),
    ],
)
def test_bad_frames_are_dropped(field, value, reason):
    frames = [quality(i) for i in range(40)]
    setattr(frames[5], field, value)
    mark_frames(frames, [], CFG)
    assert not frames[5].kept and reason in frames[5].reasons
    assert sum(f.kept for f in frames) == 39


def test_frames_near_a_tracking_jump_are_dropped():
    frames = [quality(i) for i in range(40)]
    mark_frames(frames, [10.2], CFG)
    assert not frames[10].kept and "near_tracking_jump" in frames[10].reasons
    assert frames[12].kept


def test_never_drops_below_the_keep_floor():
    frames = [quality(i, dark_frac=0.9) for i in range(100)]
    mark_frames(frames, [], CFG)
    assert sum(f.kept for f in frames) == 50  # 50% floor


# ---------- coverage ----------

def test_floor_and_ceiling_seen():
    frames = [flat_frame(i, LOOK_DOWN, 1.4, x=i * 1.0) for i in range(3)]
    frames += [flat_frame(10 + i, LOOK_UP, 1.2, x=i * 1.0) for i in range(3)]
    cov = measure_coverage(frames, CFG)
    assert cov.floor_seen and cov.ceiling_seen
    assert cov.camera_height_m == pytest.approx(1.4, abs=0.02)
    assert cov.up_frames_frac == pytest.approx(0.5)


def test_ceiling_not_seen_when_never_looking_up():
    frames = [flat_frame(i, LOOK_DOWN, 1.4, x=i * 1.0) for i in range(4)]
    cov = measure_coverage(frames, CFG)
    assert cov.floor_seen is True
    assert cov.ceiling_seen is False


def test_no_poses_means_coverage_unknown():
    frame = flat_frame(0, LOOK_DOWN, 1.4)
    frame.T_world_cam = None
    cov = measure_coverage([frame], CFG)
    assert cov.floor_seen is None and cov.ceiling_seen is None


# ---------- issues and devices ----------

def meta(device=None, duration=60.0):
    return CaptureMeta("cap", Tier.LIDAR, "stray_scanner", source_dir=None, device_model=device, duration_s=duration)


def codes(issues):
    return {i.code for i in issues}


def test_issue_codes():
    frames = [quality(i) for i in range(40)]
    mark_frames(frames, [], CFG)
    cov = Coverage(floor_seen=True, floor_area_m2=10, ceiling_seen=False, ceiling_area_m2=0)
    issues = find_issues(meta(duration=5.0), frames, cov, [], median_brightness=30.0, cfg=CFG)
    assert {"CEILING_NOT_SEEN", "LOW_LIGHT", "SHORT_CAPTURE", "DEVICE_UNKNOWN"} <= codes(issues)
    assert all(i.fix for i in issues)


def test_device_issues():
    frames = [quality(i) for i in range(40)]
    cov = Coverage()
    assert "DEVICE_UNSUPPORTED" in codes(find_issues(meta("iPhone 13 Pro"), frames, cov, [], 140, CFG))
    assert not codes(find_issues(meta("iPhone 16 Pro"), frames, cov, [], 140, CFG)) & {
        "DEVICE_UNSUPPORTED", "DEVICE_UNKNOWN"}


@pytest.mark.parametrize(
    "name, supported, lidar",
    [("iPhone 15", True, False), ("iPhone 17 Pro Max", True, True), ("iPhone 13 Pro", False, True),
     ("iPhone Air", True, False), ("Pixel 9", None, None)],
)
def test_device_lookup(name, supported, lidar):
    d = lookup_device(name)
    assert d.supported is supported and d.has_lidar is lidar


# ---------- end to end ----------

def test_run_qc_on_synthetic_capture(stray_capture, cache_root, tmp_path):
    fs = load_capture(stray_capture, cache_root=cache_root)
    result = run_qc(fs)
    r = result.report
    assert r.n_frames_in == len(fs)
    assert len(result.frameset) == r.n_frames_kept
    assert 0 < r.quality_score <= 1
    # the synthetic camera only ever sees a wall straight ahead
    assert "FLOOR_NOT_SEEN" in codes(r.issues) and r.has_errors
    json_path, md_path = write_reports(r, tmp_path / "out")
    assert json.loads(json_path.read_text(encoding="utf-8"))["capture_id"] == "abc123"
    assert "FLOOR_NOT_SEEN" in md_path.read_text(encoding="utf-8")
