from types import SimpleNamespace

import numpy as np
import pytest

from scan.measure import RoomMeasurement, WallMeasure
from scan.openings import Opening
from scan.rooms import Doorway
from scan.rooms.floormap import GridFrame
from scan.stitch import merge_openings, stitch, StitchConfig

FRAME = GridFrame(0.0, (0.0, 0.0), 0.05, (10, 10))  # uv == xz


def room(rid, u0, v0, u1, v1, inferred=()):
    """Rectangle room. Edge order: 0 bottom (v=v0), 1 right (u=u1), 2 top (v=v1), 3 left (u=u0)."""
    corners = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
    lengths = [u1 - u0, v1 - v0, u1 - u0, v1 - v0]
    walls = [WallMeasure(k, lengths[k], 0.001, [] if k in inferred else [10 * rid + k],
                         0.0 if k in inferred else 1.0, k in inferred) for k in range(4)]
    return RoomMeasurement(rid, f"room_{rid}", "room", corners, walls, (u1 - u0) * (v1 - v0), 0.01,
                           2 * sum(lengths[:2]), 2.5, 0.001, None, 0.0, corners_uv=corners)


def layout(doorways=()):
    return SimpleNamespace(floor_map=SimpleNamespace(frame=FRAME), doorways=list(doorways),
                           capture_id="synthetic", manhattan_deg=0.0)


def opening(oid, rid, wall, width, sigma=0.002, centre=(5.05, 1.5), connects=None, kind="door", rays=50000):
    return Opening(oid, rid, f"room_{rid}", wall, [], kind, 0.5, width, sigma, 2.1, 0.0, centre, 2,
                   covered=False, head_observed=True, low_evidence=False,
                   evidence={"through_rays": rays}, connects_room=connects)


def test_shared_wall_and_thickness():
    a = room(1, 0, 0, 5.0, 3)
    b = room(2, 5.1, 0, 8, 3)
    plan = stitch([a, b], [], layout())
    assert len(plan.shared_walls) == 1
    assert plan.shared_walls[0].thickness_m == pytest.approx(0.10, abs=1e-6)
    assert plan.overlaps == []
    assert plan.footprint_m2 == pytest.approx(3 * 8, abs=0.3)  # wall gap bridged


def test_inferred_edge_pulled_out_of_neighbour():
    a = room(1, 0, 0, 5.4, 3, inferred={1})  # open right side reaches into room 2
    b = room(2, 5.1, 0, 8, 3)
    plan = stitch([a, b], [], layout())
    assert plan.overlaps == []
    right = np.array(plan.rooms[0].corners_uv)[:, 0].max()
    assert right == pytest.approx(5.1 - 0.10, abs=1e-6)  # behind room 2's wall, default thickness
    assert plan.rooms[0].floor_area_m2 == pytest.approx(5.0 * 3, abs=1e-6)
    assert plan.adjusted_edges == [(1, 1, pytest.approx(0.4, abs=1e-6))]


def test_observed_walls_are_never_moved_and_overlap_is_reported():
    a = room(1, 0, 0, 5.4, 3)
    b = room(2, 5.1, 0, 8, 3)
    plan = stitch([a, b], [], layout())
    assert plan.adjusted_edges == []
    assert plan.overlaps and plan.overlaps[0][:2] == (1, 2)


def test_two_views_that_agree_are_averaged():
    ops = [opening(0, 1, 1, 0.800, connects=2), opening(1, 2, 3, 0.804, connects=1)]
    (m,) = merge_openings(ops, StitchConfig())
    assert m.views == 2 and m.views_agree
    assert m.width_m == pytest.approx(0.802, abs=1e-3)
    assert m.rooms == [1, 2]


def test_two_views_that_disagree_take_the_clear_opening():
    ops = [opening(0, 1, 1, 0.902, connects=2), opening(1, 2, 3, 0.802, connects=1)]
    (m,) = merge_openings(ops, StitchConfig())
    assert m.views_agree is False
    assert m.width_m == pytest.approx(0.802)
    assert m.width_sigma_m == pytest.approx(0.05)


def test_windows_far_apart_stay_separate():
    ops = [opening(0, 1, 0, 1.2, centre=(1, 0), kind="window"), opening(1, 1, 2, 1.0, centre=(1, 3), kind="window")]
    assert len(merge_openings(ops, StitchConfig())) == 2


def test_adjacency_connectivity_and_unmeasured_doorway():
    rooms = [room(1, 0, 0, 5.0, 3), room(2, 5.1, 0, 8, 3), room(3, 8.1, 0, 10, 3)]
    ops = [opening(0, 1, 1, 0.8, centre=(5.05, 1.5), connects=2)]
    doorways = [Doorway(0, 1, 2, "door", (5.05, 1.5), 0.5), Doorway(1, 2, 3, "door", (8.05, 1.5), 0.5)]
    plan = stitch(rooms, ops, layout(doorways))
    assert plan.connected_components == 1
    assert plan.unmeasured_doorways == [1]  # room 2 <-> 3 has no measured opening: a likely miss
    assert (2, 3, "doorway") in plan.adjacency


def test_disconnected_rooms_are_reported():
    plan = stitch([room(1, 0, 0, 3, 3), room(2, 6, 0, 9, 3)], [], layout())
    assert plan.connected_components == 2
