from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from scan.ingest import load_capture
from scan.models import MODELS
from scan.semantics import SemanticsConfig, Surface, run_semantics, write_overlays
from scan.semantics.check import geometry_agreement
from scan.semantics.classes import build_lookup
from scan.semantics.segmenter import SegFormerSegmenter
from scan.semantics.upright import from_upright, to_upright, upright_quarter_turns
from tests.conftest import REPO_ROOT
from tests.test_qc import LOOK_DOWN, LOOK_UP, flat_frame

SMALL = SemanticsConfig(label_size=(32, 24), max_frames=100)
UPRIGHT = np.diag([1.0, -1.0, -1.0])  # level camera: image-down is world-down


class FakeSegmenter:
    """Top 30% of the upright image is ceiling, bottom 30% floor, the rest wall."""

    def __init__(self, name="fake"):
        self.name = name
        self.calls = 0

    def predict(self, rgb, out_size):
        self.calls += 1
        w, h = out_size
        labels = np.full((h, w), Surface.WALL, np.uint8)
        labels[: int(0.3 * h)] = Surface.CEILING
        labels[int(0.7 * h) :] = Surface.FLOOR
        return labels, np.full((h, w), 0.8, np.float32)


def pose(R):
    T = np.eye(4)
    T[:3, :3] = R
    return T


# ---------- class mapping ----------

def test_lookup_maps_by_name_and_glass_is_not_window():
    lookup = build_lookup({0: "wall", 1: "glass", 2: "screen door", 3: "sofa", 4: "Mirror "})
    assert list(lookup) == [Surface.WALL, Surface.OTHER, Surface.DOOR, Surface.OTHER, Surface.MIRROR]


# ---------- upright rotation ----------

def test_level_camera_needs_no_turn():
    assert upright_quarter_turns(pose(UPRIGHT)) == 0


def test_upside_down_camera_needs_half_turn():
    assert upright_quarter_turns(pose(np.eye(3))) == 2


def test_portrait_camera_needs_quarter_turn():
    # image-right points at the floor, so world-up is image-left -> one clockwise turn
    R = np.column_stack([[0, -1, 0], [-1, 0, 0], [0, 0, -1]]).astype(float)
    assert upright_quarter_turns(pose(R)) == 3


@pytest.mark.parametrize("k", [0, 1, 2, 3])
def test_rotation_round_trip(k):
    a = np.arange(12).reshape(3, 4)
    assert np.array_equal(from_upright(to_upright(a, k), k), a)


# ---------- run_semantics ----------

def test_labels_attached_and_rotated_back(stray_capture, cache_root):
    fs = load_capture(stray_capture, cache_root=cache_root)
    fake = FakeSegmenter()
    result = run_semantics(fs, SMALL, segmenter=fake, cache_root=cache_root / "sem")
    labelled = [f for f in result.frameset.frames if f.has_labels]
    assert len(labelled) == len(fs) == fake.calls
    labels = labelled[0].labels()
    assert labels.shape == (24, 32)
    # synthetic poses are identity = upside down, so the "ceiling" band ends up at the bottom
    assert (labels[-1] == Surface.CEILING).all() and (labels[0] == Surface.FLOOR).all()
    assert labelled[0].label_conf()[0, 0] == pytest.approx(0.8, abs=0.01)
    assert result.summary.n_frames_labelled == len(fs)
    assert result.summary.class_shares["wall"] == pytest.approx(0.4, abs=0.05)


def test_second_run_uses_cache(stray_capture, cache_root):
    fs = load_capture(stray_capture, cache_root=cache_root)
    run_semantics(fs, SMALL, segmenter=FakeSegmenter(), cache_root=cache_root / "sem")
    again = FakeSegmenter()
    result = run_semantics(fs, SMALL, segmenter=again, cache_root=cache_root / "sem")
    assert again.calls == 0
    assert result.summary.n_frames_from_cache == len(fs)


def test_cache_invalidated_when_model_changes(stray_capture, cache_root):
    fs = load_capture(stray_capture, cache_root=cache_root)
    run_semantics(fs, SMALL, segmenter=FakeSegmenter("a"), cache_root=cache_root / "sem")
    other = FakeSegmenter("b")
    run_semantics(fs, SMALL, segmenter=other, cache_root=cache_root / "sem")
    assert other.calls == len(fs)


def test_blurry_frames_skipped_and_budget_respected(stray_capture, cache_root):
    fs = load_capture(stray_capture, cache_root=cache_root)
    frames = [replace(f, rgb_ok=(i != 0)) for i, f in enumerate(fs.frames)]
    fs = fs.with_frames(frames)
    cfg = SemanticsConfig(label_size=(32, 24), max_frames=2)
    result = run_semantics(fs, cfg, segmenter=FakeSegmenter(), cache_root=cache_root / "sem")
    labelled = [f for f in result.frameset.frames if f.has_labels]
    assert len(labelled) == 2
    assert not result.frameset.frames[0].has_labels
    assert len(result.frameset) == len(fs)  # unlabelled frames are kept for geometry


def test_overlays_written(stray_capture, cache_root, tmp_path):
    fs = load_capture(stray_capture, cache_root=cache_root)
    result = run_semantics(fs, SMALL, segmenter=FakeSegmenter(), cache_root=cache_root / "sem")
    paths = write_overlays(result.frameset, tmp_path / "ov", count=2)
    assert len(paths) == 2 and all(p.exists() for p in paths)


# ---------- geometry self-check ----------

def labelled(frame, surface):
    lab = np.full((48, 64), surface, np.uint8)
    return replace(frame, label_size=(64, 48), _labels=lambda: lab)


def test_agreement_high_for_correct_labels():
    frames = [labelled(flat_frame(0, LOOK_DOWN, 1.4), Surface.FLOOR),
              labelled(flat_frame(1, LOOK_UP, 1.2), Surface.CEILING)]
    agreement = geometry_agreement(frames)
    assert agreement["floor"]["agreement"] == 1.0
    assert agreement["ceiling"]["agreement"] == 1.0


def test_agreement_low_for_wrong_labels():
    frames = [labelled(flat_frame(0, LOOK_DOWN, 1.4), Surface.WALL)]
    assert geometry_agreement(frames)["wall"]["agreement"] == 0.0


# ---------- real model (optional) ----------

@pytest.mark.sample
def test_segformer_on_sample_frame(tmp_path):
    capture = REPO_ROOT / "single_scan_with_ceiling"
    if not capture.exists() or not (MODELS["segformer-b2-ade"].local_dir / "config.json").exists():
        pytest.skip("sample capture or SegFormer weights not present")
    fs = load_capture(capture, cache_root=tmp_path / "cache")
    frames = fs.frames[::40]
    result = run_semantics(fs.with_frames(frames), SemanticsConfig(max_frames=len(frames)),
                           cache_root=tmp_path / "sem")
    agreement = result.summary.agreement
    assert agreement["wall"]["agreement"] > 0.8
    assert agreement["floor"]["agreement"] > 0.6
