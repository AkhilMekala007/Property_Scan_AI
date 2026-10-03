from pathlib import Path

import numpy as np
import pytest

from scan.adapters.base import AdapterConfig, KeyframeConfig
from scan.adapters.stray import select_keyframes
from scan.core.geometry import quat_to_rotmat
from scan.core.types import Tier
from scan.ingest import load_capture
from tests.conftest import DEPTH_H, DEPTH_W, RGB_H, RGB_W, REPO_ROOT, write_stray_capture


def test_loads_into_frameset(stray_capture, cache_root):
    fs = load_capture(stray_capture, cache_root=cache_root)
    assert fs.meta.tier is Tier.LIDAR
    assert fs.meta.n_frames_raw == 10
    assert fs.has_poses and fs.has_depth
    # camera moves 6 cm per frame -> a keyframe every 2nd frame at the 10 cm threshold
    assert [f.index for f in fs.frames] == [0, 2, 4, 6, 8]


def test_frame_contents(stray_capture, cache_root):
    frame = load_capture(stray_capture, cache_root=cache_root).frames[1]
    assert frame.rgb().shape == (RGB_H, RGB_W, 3)
    depth = frame.depth()
    assert depth.shape == (DEPTH_H, DEPTH_W)
    assert np.isnan(depth[0, 0])  # 0 mm dropout -> NaN
    assert depth[5, 5] == pytest.approx(2.0)  # millimetres -> metres
    assert frame.depth_intrinsics.width == DEPTH_W
    assert np.allclose(frame.T_world_cam[:3, 3], [0.12, 0.0, 0.0])


def test_depth_sigma_follows_confidence(stray_capture, cache_root):
    frame = load_capture(stray_capture, cache_root=cache_root).frames[0]
    sigma = frame.depth_sigma()
    high = sigma[5, 5]
    assert high == pytest.approx(0.005 + 0.005 * 2.0**2)
    assert sigma[1, 5] == pytest.approx(3 * high)  # confidence 1
    assert np.isnan(sigma[2, 5])  # confidence 0
    assert np.isnan(sigma[0, 0])  # no depth


def test_device_unknown_warns_and_override_clears_it(stray_capture, cache_root):
    fs = load_capture(stray_capture, cache_root=cache_root)
    assert fs.meta.device_model is None
    assert any("--device" in w for w in fs.meta.warnings)
    fs = load_capture(stray_capture, cache_root=cache_root, config=AdapterConfig(device_model="iPhone 17 Pro"))
    assert fs.meta.device_model == "iPhone 17 Pro"
    assert not any("--device" in w for w in fs.meta.warnings)


def test_frame_count_mismatch_warns(stray_capture, cache_root):
    for i in (7, 8, 9):
        (stray_capture / "depth" / f"{i:06d}.png").unlink()
    fs = load_capture(stray_capture, cache_root=cache_root)
    assert fs.meta.n_frames_raw == 7
    assert any("frame counts differ" in w for w in fs.meta.warnings)


def test_frame_cache_is_reused(stray_capture, cache_root):
    load_capture(stray_capture, cache_root=cache_root)
    jpg = cache_root / "abc123" / "rgb" / "000002.jpg"
    before = jpg.stat().st_mtime_ns
    load_capture(stray_capture, cache_root=cache_root)
    assert jpg.stat().st_mtime_ns == before


def test_keyframes_capped_evenly():
    positions = np.array([[i * 0.2, 0, 0] for i in range(100)], float)
    rotations = [np.eye(3)] * 100
    keys = select_keyframes(positions, rotations, KeyframeConfig(max_keyframes=10))
    assert len(keys) == 10
    assert keys[0] == 0 and keys[-1] == 99


def test_keyframes_on_rotation_only():
    positions = np.zeros((4, 3))
    rotations = [quat_to_rotmat(0, np.sin(np.radians(a / 2)), 0, np.cos(np.radians(a / 2))) for a in (0, 3, 6, 9)]
    keys = select_keyframes(positions, rotations, KeyframeConfig(min_rotation_deg=5.0))
    assert keys == [0, 2]


def test_keyframes_are_deterministic(tmp_path):
    a = load_capture(write_stray_capture(tmp_path / "a"), cache_root=tmp_path / "c1")
    b = load_capture(write_stray_capture(tmp_path / "b"), cache_root=tmp_path / "c2")
    assert [f.index for f in a.frames] == [f.index for f in b.frames]


SAMPLES = [
    REPO_ROOT / "single_room",
    REPO_ROOT / "single_scan_floor_only",
    REPO_ROOT / "single_scan_with_ceiling",
]


@pytest.mark.sample
@pytest.mark.parametrize("capture", SAMPLES, ids=lambda p: p.name)
def test_sample_capture_loads(capture: Path, tmp_path):
    if not capture.exists():
        pytest.skip(f"{capture.name} not present locally")
    fs = load_capture(capture, cache_root=tmp_path / "cache")
    assert fs.meta.tier is Tier.LIDAR
    assert 0 < len(fs) <= 400
    assert fs.frames[0].intrinsics.width == 1920
    assert fs.frames[0].depth_size == (256, 192)
    assert not any("frame counts differ" in w for w in fs.meta.warnings)
