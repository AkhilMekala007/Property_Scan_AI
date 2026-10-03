from dataclasses import replace

import numpy as np
import pytest

from scan.core.types import CaptureMeta, FrameSet, Tier
from scan.semantics.classes import Surface
from scan.structure import run_structure
from scan.structure.planes import (
    StructureConfig,
    StructureStats,
    find_ceilings,
    find_floor,
    find_walls,
    manhattan_angle,
)
from scan.structure.voxels import UNKNOWN, VoxelGrid, fuse
from tests.test_qc import LOOK_DOWN, flat_frame

CFG = StructureConfig()
W, D, H = 4.0, 3.0, 2.6  # synthetic room: width (x), depth (z), ceiling height
ROT_DEG = 20.0
STEP = 0.02


def _surface(u_len, v_len):
    u, v = np.meshgrid(np.arange(0, u_len, STEP) + STEP / 2, np.arange(0, v_len, STEP) + STEP / 2)
    return u.ravel(), v.ravel()


def box_room(rot_deg=ROT_DEG, noise=0.003, seed=0):
    """Voxel grid of a W x D room, floor at y=0, ceiling at y=H, normals pointing inside."""
    rng = np.random.default_rng(seed)
    pts, nrm, lab = [], [], []

    def add(p, n, label):
        pts.append(p)
        nrm.append(np.repeat([n], len(p), 0))
        lab.append(np.full(len(p), label))

    u, v = _surface(W, D)
    add(np.column_stack([u, np.zeros_like(u), v]), [0, 1, 0], Surface.FLOOR)
    add(np.column_stack([u, np.full_like(u, H), v]), [0, -1, 0], Surface.CEILING)
    u, v = _surface(W, H)
    add(np.column_stack([u, v, np.zeros_like(u)]), [0, 0, 1], Surface.WALL)
    add(np.column_stack([u, v, np.full_like(u, D)]), [0, 0, -1], Surface.WALL)
    u, v = _surface(D, H)
    add(np.column_stack([np.zeros_like(u), v, u]), [1, 0, 0], Surface.WALL)
    add(np.column_stack([np.full_like(u, W), v, u]), [-1, 0, 0], Surface.WALL)

    P, N, L = np.concatenate(pts), np.concatenate(nrm).astype(float), np.concatenate(lab)
    th = np.radians(rot_deg)
    R = np.array([[np.cos(th), 0, -np.sin(th)], [0, 1, 0], [np.sin(th), 0, np.cos(th)]])
    P = P @ R.T + rng.normal(0, noise, P.shape) + np.array([1.0, -1.4, 2.0])
    N = N @ R.T
    votes = np.zeros((len(P), len(Surface)), np.float32)
    votes[np.arange(len(P)), L] = 1
    return VoxelGrid(STEP, P, N, np.ones(len(P)), np.full(len(P), 5, np.int32), votes, L.astype(np.int8))


# ---------- voxel fusion ----------

def test_fuse_merges_repeated_views_and_votes():
    lab = np.full((48, 64), Surface.FLOOR, np.uint8)
    frame = replace(flat_frame(0, LOOK_DOWN, 1.4), label_size=(64, 48), _labels=lambda: lab,
                    _label_conf=lambda: np.full((48, 64), 0.9, np.float32))
    grid = fuse([frame, frame, frame], min_count=3)
    assert len(grid) > 100
    assert (grid.counts >= 3).all()
    assert np.allclose(grid.centers[:, 1], -1.4, atol=1e-6)
    assert (grid.normals[:, 1] > 0.99).all()  # points back up at the camera
    assert (grid.labels == Surface.FLOOR).all()


def test_fuse_min_count_drops_rare_sightings():
    frame = flat_frame(0, LOOK_DOWN, 1.4)
    assert len(fuse([frame, frame], min_count=3)) == 0  # every voxel seen only twice
    assert len(fuse([frame, frame], min_count=2)) > 100


def test_fuse_without_posed_depth_is_an_error():
    with pytest.raises(ValueError, match="no posed depth"):
        fuse([replace(flat_frame(0, LOOK_DOWN, 1.4), T_world_cam=None)])


def test_unlabelled_frames_give_unknown_labels():
    grid = fuse([flat_frame(0, LOOK_DOWN, 1.4)] * 3)
    assert (grid.labels == UNKNOWN).all()


# ---------- planes on a synthetic room ----------

def test_manhattan_angle_recovers_rotation():
    g = box_room()
    vertical = np.abs(g.normals[:, 1]) < 0.2
    assert manhattan_angle(g.normals[vertical], g.counts[vertical].astype(float)) == pytest.approx(ROT_DEG, abs=0.5)


def test_floor_and_ceiling_height():
    g = box_room()
    floor = find_floor(g, None, CFG)
    assert floor.tilt_deg < 0.2
    ceilings = find_ceilings(g, floor, CFG)
    assert len(ceilings) == 1
    x, z = ceilings[0].centroid_xz
    assert ceilings[0].y_at(x, z) - floor.y_at(x, z) == pytest.approx(H, abs=0.003)


def test_four_walls_with_correct_spacing_and_length():
    g = box_room()
    stats = StructureStats()
    walls = find_walls(g, ROT_DEG, CFG, stats)
    assert len(walls) == 4
    assert all(w.snapped for w in walls)
    assert stats.n_wall_voxels_assigned == stats.n_wall_voxels
    by_dir = {}
    for w in walls:
        by_dir.setdefault(round(w.angle_deg) % 180, []).append(w)
    spacings = sorted(abs(a.offset_m + b.offset_m) for a, b in by_dir.values())  # opposite normals
    assert spacings == pytest.approx([D, W], abs=0.005)
    lengths = sorted(w.length_seen_m for w in walls)
    assert lengths == pytest.approx([D, D, W, W], abs=0.05)
    for w in walls:
        assert w.coverage > 0.9
        assert w.rms_m == pytest.approx(0.003, abs=0.0015)


def test_wall_split_at_gap():
    g = box_room()
    # cut a 1 m gap out of one long wall (z = 0 face before rotation, normal angle = ROT + 90)
    th = np.radians(ROT_DEG)
    local = (g.centers - [1.0, -1.4, 2.0]) @ np.array(
        [[np.cos(th), 0, -np.sin(th)], [0, 1, 0], [np.sin(th), 0, np.cos(th)]])
    gap = (np.abs(local[:, 2]) < 0.02) & (local[:, 0] > 1.5) & (local[:, 0] < 2.5) & (g.labels == Surface.WALL)
    walls = find_walls(g.subset(~gap), ROT_DEG, CFG, StructureStats())
    assert len(walls) == 5


# ---------- end to end ----------

def test_run_structure_respects_ceiling_not_seen():
    frames = [flat_frame(i, LOOK_DOWN, 1.4) for i in range(3)]  # same view, so voxels pass min_count
    meta = CaptureMeta("synthetic", Tier.LIDAR, "stray_scanner", source_dir=None)
    model = run_structure(FrameSet(meta, frames), floor_hint_y=-1.4, ceiling_seen=False)
    assert model.floor is not None and model.floor.y_at_centroid == pytest.approx(-1.4, abs=1e-3)
    assert model.ceilings == [] and "not observed" in model.ceiling_note
    assert model.to_dict()["ceiling_heights"] == []
