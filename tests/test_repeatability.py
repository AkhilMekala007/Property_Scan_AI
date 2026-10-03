"""Regression tests for the repeatability fixes (each failure mode found with `scan repeat`)."""

import numpy as np
import pytest

from scan.measure.polygon import WallLine, arrangement_polygon, polygon_area
from scan.repeat import RunSummary, compare, perturb
from scan.rooms.floormap import GridFrame
from scan.rooms.segment import SegmentConfig, seed_regions
from scan.semantics.classes import Surface
from scan.structure.planes import StructureConfig, StructureStats, _deepest_peak, find_walls
from tests.test_rooms import Plan
from tests.test_structure import box_room

CELL = 0.05
FRAME = GridFrame(0.0, (0.0, 0.0), CELL, (200, 200))


def wall(axis, coord, sign, span, wid):
    return WallLine(wid, axis, coord, sign, span, 0.0002, 1000.0)


# ---------- C7a: the wall, not the skirting board in front of it ----------

def test_deepest_strong_peak_wins():
    smooth = np.zeros(40)
    smooth[10] = 80.0  # the wall
    smooth[13] = 100.0  # a skirting board 3 cm into the room, slightly stronger
    assert _deepest_peak(smooth, 13, window=8, ratio=0.4) == 10


def test_weak_deeper_bump_is_ignored():
    smooth = np.zeros(40)
    smooth[10] = 20.0  # noise behind the wall
    smooth[13] = 100.0
    assert _deepest_peak(smooth, 13, window=8, ratio=0.4) == 13


def test_wall_offset_stable_with_skirting_in_front():
    g = box_room(rot_deg=0.0)
    # add a skirting board 3 cm in front of the x = 0 wall (normal +x), bottom 10 cm, extra dense
    skirting = (np.abs(g.centers[:, 0] - 1.0) < 1e-6) & (g.centers[:, 1] < -1.4 + 0.10)
    g.centers[skirting, 0] += 0.03
    g.counts[skirting] *= 6
    walls = find_walls(g, 0.0, StructureConfig(), StructureStats())
    west = [w for w in walls if abs(w.normal_xz[0] - 1) < 1e-6]
    assert len(west) == 1 and west[0].offset_m == pytest.approx(1.0, abs=0.006)


# ---------- C7b: slivers and pieces ----------

def test_sliver_thinner_than_a_cell_does_not_split_the_room():
    mask = np.zeros(FRAME.shape, bool)
    mask[21:79, 21:139] = True  # one 6 x 3 m region
    lines = [wall("u", 1.0, +1, (1, 4), 0), wall("u", 7.0, -1, (1, 4), 1),
             wall("v", 1.0, +1, (1, 7), 2), wall("v", 4.0, -1, (1, 7), 3),
             # two wall faces 3 cm apart crossing the room (a sliver narrower than a 5 cm cell)
             wall("u", 4.74, -1, (1.0, 1.6), 4), wall("u", 4.77, +1, (1.0, 1.6), 5)]
    corners, _ = arrangement_polygon(mask, FRAME, lines)
    assert polygon_area(corners) == pytest.approx(18.0, abs=0.05)


def test_half_outside_rectangle_does_not_inflate_the_room():
    mask = np.zeros(FRAME.shape, bool)
    mask[21:79, 21:79] = True  # 3 x 3 m room...
    mask[21:30, 79:120] = True  # ...with a thin protrusion to the east
    lines = [wall("u", 1.0, +1, (1, 4), 0), wall("v", 1.0, +1, (1, 6), 1), wall("v", 4.0, -1, (1, 4), 2),
             wall("u", 4.0, -1, (1, 4), 3), wall("u", 6.0, -1, (1, 1.5), 4), wall("v", 1.5, -1, (4, 6), 5)]
    corners, _ = arrangement_polygon(mask, FRAME, lines)
    assert polygon_area(corners) == pytest.approx(9.0 + 2.0 * 0.5, abs=0.1)


# ---------- C6: corridors keep their seed ----------

def test_one_metre_corridor_keeps_its_seed():
    p = Plan().room(0, 0, 4, 3).room(4, 0, 9, 1.0).room(9, 0, 12, 3)
    p.vwall(4, 0, 1, gap=(0.1, 0.9), door=True).vwall(9, 0, 1, gap=(0.1, 0.9), door=True)
    seeds = seed_regions(p.build(), SegmentConfig())
    assert seeds.max() == 3


# ---------- harness ----------

def test_perturbation_is_small_and_deterministic(stray_capture, cache_root):
    from scan.ingest import load_capture

    fs = load_capture(stray_capture, cache_root=cache_root)
    a, b = perturb(fs, seed=3), perturb(fs, seed=3)
    for fa, fb, f0 in zip(a.frames, b.frames, fs.frames):
        assert np.allclose(fa.T_world_cam, fb.T_world_cam)
        assert np.linalg.norm(fa.T_world_cam[:3, 3] - f0.T_world_cam[:3, 3]) < 0.03
        assert fa.T_world_cam[1, 1] == pytest.approx(f0.T_world_cam[1, 1])  # gravity untouched


def test_identical_runs_report_zero_spread():
    room = {"centroid": np.array([1.0, 1.0]), "area": 12.0, "ceiling": 2.5,
            "walls": [{"mid": np.array([1.0, 0.0]), "dir": np.array([1.0, 0.0]), "length": 4.0}]}
    run = RunSummary([room], [], 1, 0)
    rep = compare([run, run, run])
    assert rep.headline()["rooms_stable"] and rep.wall_length_spread_m == [0.0]
