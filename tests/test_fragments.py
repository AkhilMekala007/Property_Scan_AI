from types import SimpleNamespace

import numpy as np

from scan.fragments import Fragment, FragmentRun, _rot, place_fragments


def _room(rid, corners):
    return SimpleNamespace(id=rid, corners_xz=[tuple(c) for c in corners], floor_area_m2=10.0)


def _op(room_id, wall_index, center, width=0.9):
    return SimpleNamespace(room_id=room_id, wall_index=wall_index, center_xz=tuple(center), width_m=width, kind="door")


def _run(name, rooms, ops, manhattan=0.0):
    res = SimpleNamespace(layout=SimpleNamespace(manhattan_deg=manhattan))
    return FragmentRun(Fragment(name, frameset=None), res, rooms, ops)


def test_door_match_restores_relative_pose():
    # room A: x 0..4, z 0..3; door on its east wall (x = 4) at z = 1.5
    a = _run("A", [_room(1, [(0, 0), (4, 0), (4, 3), (0, 3)])], [_op(1, 1, (4, 1.5))])
    # room B in the true frame: x 4.1..7.1, z 0..3, door on its west wall at z = 1.5
    true_b = np.array([(4.1, 0), (7.1, 0), (7.1, 3), (4.1, 3)], float)
    true_door = np.array([4.1, 1.5])
    # B's own reconstruction frame: rotated 90 degrees and shifted
    R, t = _rot(np.pi / 2), np.array([10.0, -5.0])
    b_corners = true_b @ R.T + t
    b_door = R @ true_door + t
    b = _run("B", [_room(1, b_corners)], [_op(1, 3, b_door, width=0.88)])
    # make A the larger (anchor)
    a.rooms[0].floor_area_m2 = 12.0
    log = place_fragments([a, b])
    assert b.placed and b.how.startswith("door")
    placed = np.array(b.rooms[0].corners_xz) @ _rot(b.rot).T + b.t
    assert np.allclose(placed, true_b, atol=1e-6), (placed, log)


def test_unmatched_fragment_is_placed_apart():
    a = _run("A", [_room(1, [(0, 0), (4, 0), (4, 3), (0, 3)])], [_op(1, 1, (4, 1.5))])
    a.rooms[0].floor_area_m2 = 12.0
    c = _run("C", [_room(1, [(0, 0), (2, 0), (2, 2), (0, 2)])], [])
    place_fragments([a, c])
    assert not c.placed and "apart" in c.how
    placed = np.array(c.rooms[0].corners_xz) @ _rot(c.rot).T + c.t
    assert placed[:, 0].min() > 4.0
