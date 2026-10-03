import numpy as np
import pytest

from scan.core.types import Trajectory
from scan.rooms import _drop_unreached
from scan.rooms.floormap import FloorMap, GridFrame
from scan.rooms.segment import (
    SegmentConfig,
    drop_small_rooms,
    find_contacts,
    grow,
    is_corridor,
    seed_regions,
)

CELL = 0.05
CFG = SegmentConfig()


def cells(m):
    return int(round(m / CELL))


class Plan:
    """Draw rooms on a grid in metres: inside areas, walls, door frames."""

    def __init__(self, w_m=12.0, h_m=8.0):
        shape = (cells(h_m), cells(w_m))
        self.inside = np.zeros(shape, bool)
        self.barrier = np.zeros(shape, bool)
        self.door = np.zeros(shape, bool)
        self.frame = GridFrame(0.0, (0.0, 0.0), CELL, shape)

    def room(self, x0, z0, x1, z1):
        self.inside[cells(z0):cells(z1), cells(x0):cells(x1)] = True
        return self

    def vwall(self, x, z0, z1, gap=None, door=False):
        """Wall along z at x (0.1 m thick); optional gap (z_from, z_to) for a doorway."""
        c0, c1 = cells(x - 0.05), cells(x + 0.05)
        self.barrier[cells(z0):cells(z1), c0:c1] = True
        self.inside[cells(z0):cells(z1), c0:c1] = False
        if gap:
            g0, g1 = cells(gap[0]), cells(gap[1])
            self.barrier[g0:g1, c0:c1] = False
            self.inside[g0:g1, c0:c1] = True
            if door:  # door frame jambs either side of the gap
                self.door[g0 - 1:g0 + 1, c0 - 2:c1 + 2] = True
                self.door[g1 - 1:g1 + 1, c0 - 2:c1 + 2] = True
        return self

    def build(self):
        return FloorMap(self.frame, self.inside, self.barrier, self.door, self.inside.copy(),
                        np.full(self.inside.shape, np.nan, np.float32))


def segment(fmap):
    labels = grow(seed_regions(fmap, CFG), fmap.inside)
    return drop_small_rooms(labels, CELL, CFG.min_room_m2)


def area(labels, rid):
    return (labels == rid).sum() * CELL * CELL


# ---------- splitting ----------

def test_two_rooms_split_at_a_door():
    fmap = Plan().room(0, 0, 4, 3).room(4, 0, 7, 3).vwall(4, 0, 3, gap=(1.0, 1.8), door=True).build()
    labels = segment(fmap)
    assert labels.max() == 2
    assert (labels[fmap.inside] > 0).all()  # every inside cell belongs to a room
    assert sorted([area(labels, 1), area(labels, 2)]) == pytest.approx([9.0, 12.0], abs=0.4)
    contacts = find_contacts(labels, CELL, CFG.min_doorway_m)
    assert len(contacts) == 1
    assert contacts[0].width_m == pytest.approx(0.8, abs=0.15)


def test_narrow_opening_without_door_frame_still_splits():
    fmap = Plan().room(0, 0, 4, 3).room(4, 0, 7, 3).vwall(4, 0, 3, gap=(1.0, 1.5)).build()
    assert segment(fmap).max() == 2


def test_wide_opening_is_one_open_plan_room():
    fmap = Plan().room(0, 0, 4, 3).room(4, 0, 7, 3).vwall(4, 0, 3, gap=(0.5, 2.5)).build()
    assert segment(fmap).max() == 1


def test_corridor_between_rooms_is_its_own_room():
    p = Plan().room(0, 0, 4, 3).room(4, 0, 9, 1).room(9, 0, 12, 3)
    p.vwall(4, 0, 1, gap=(0.1, 0.9), door=True).vwall(9, 0, 1, gap=(0.1, 0.9), door=True)
    fmap = p.build()
    labels = segment(fmap)
    assert labels.max() == 3
    corridor = [rid for rid in range(1, 4) if is_corridor(labels == rid, CFG.corridor_aspect)]
    assert len(corridor) == 1
    assert area(labels, corridor[0]) == pytest.approx(5.0, abs=0.5)
    assert len(find_contacts(labels, CELL, CFG.min_doorway_m)) == 2


def test_rooms_behind_a_solid_wall_do_not_touch():
    fmap = Plan().room(0, 0, 4, 3).room(4, 0, 7, 3).vwall(4, 0, 3).build()
    labels = segment(fmap)
    assert labels.max() == 2
    assert find_contacts(labels, CELL, CFG.min_doorway_m) == []


# ---------- clean-up ----------

def test_small_fragment_merged_into_neighbour():
    labels = np.zeros((40, 40), np.int32)
    labels[:, :30] = 1
    labels[:, 30:34] = 2  # 2.0 m x 0.2 m = 0.4 m2, below the 1 m2 minimum
    out = drop_small_rooms(labels, CELL, min_m2=1.0)
    assert out.max() == 1 and (out[:, :34] == 1).all()


def test_area_seen_only_from_outside_is_dropped():
    fmap = Plan().room(0, 0, 4, 3).room(6, 0, 8, 2).build()  # second room: no doorway, never walked
    labels = segment(fmap)
    assert labels.max() == 2
    walk = np.column_stack([np.linspace(0.5, 3.5, 50), np.zeros(50), np.full(50, 1.5)])
    traj = Trajectory(np.arange(50.0), walk, np.repeat(np.eye(3)[None], 50, 0))
    kept, dropped = _drop_unreached(labels, find_contacts(labels, CELL, 0.4), fmap, traj, CELL)
    assert kept.max() == 1
    assert dropped == [pytest.approx(4.0, abs=0.1)]


# ---------- grid frame ----------

def test_grid_frame_round_trip_with_rotation():
    frame = GridFrame(30.0, (-5.0, -5.0), CELL, (400, 400))
    xz = np.array([[1.23, -0.77], [3.0, 2.0]])
    back = frame.cell_to_xz(frame.xz_to_cell(xz))
    assert np.abs(back - xz).max() <= CELL
    # a wall direction at the manhattan angle maps to the grid's u axis
    d = np.array([[np.cos(np.radians(30)), np.sin(np.radians(30))]])
    assert np.allclose(frame.xz_to_uv(d), [[1.0, 0.0]])
