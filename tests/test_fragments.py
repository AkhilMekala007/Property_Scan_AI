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


def test_join_chunks_recovers_similarity():
    from scipy.spatial.transform import Rotation

    from scan.multiview import MultiViewResult, join_chunks

    rng = np.random.default_rng(1)
    true = []
    for i in range(12):  # a walking path with turning cameras
        T = np.eye(4)
        T[:3, :3] = Rotation.from_euler("y", 15 * i, degrees=True).as_matrix()
        T[:3, 3] = [0.3 * i, 0.0, 0.1 * i * i / 10]
        true.append(T)

    def chunk(ids, s, R, t):
        G = np.eye(4)
        G[:3, :3], G[:3, 3] = R, t
        Ts = []
        for i in ids:
            T = np.linalg.inv(G) @ true[i]  # express in the chunk's own frame ...
            T[:3, 3] /= s  # ... with its own scale
            Ts.append(T)
        n = len(ids)
        depth = np.full((n, 20, 20), 2.0) / s  # the same scene depth, in the chunk's own units
        return ids, MultiViewResult(depth, np.ones((n, 20, 20)), np.array(Ts), np.tile(np.eye(3), (n, 1, 1)))

    c0 = chunk(list(range(0, 8)), 1.0, np.eye(3), np.zeros(3))
    c1 = chunk(list(range(5, 12)), 0.4, Rotation.from_euler("xyz", [5, 70, -3], degrees=True).as_matrix(),
               rng.normal(size=3))
    poses, scales, notes = join_chunks([c0, c1])
    for i in range(12):
        assert np.allclose(poses[i], true[i], atol=1e-6), i
    assert abs(scales[11] - 0.4) < 1e-6 and scales[0] == 1.0
