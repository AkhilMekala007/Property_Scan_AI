import numpy as np
import pytest

from scan.measure.polygon import (
    INFERRED_SIGMA_M,
    PolygonConfig,
    WallLine,
    arrangement_polygon,
    fallback_lines,
    polygon_area,
)
from scan.rooms.floormap import GridFrame

CELL = 0.05
FRAME = GridFrame(0.0, (0.0, 0.0), CELL, (200, 200))  # 10 m x 10 m, uv == xz


def region(*rects, gap=0.05):
    """Room region from (u0, v0, u1, v1) rectangles in metres, stopping ``gap`` short of the walls
    (the floor map never quite reaches the wall face)."""
    mask = np.zeros(FRAME.shape, bool)
    for u0, v0, u1, v1 in rects:
        mask[int((v0 + gap) / CELL):int((v1 - gap) / CELL), int((u0 + gap) / CELL):int((u1 - gap) / CELL)] = True
    return mask


def wall(axis, coord, sign, span, wid, sigma=0.0002):
    return WallLine(wid, axis, coord, sign, span, sigma, 1000.0)


def box_walls(u0, v0, u1, v1, ids=(0, 1, 2, 3)):
    return [wall("u", u0, +1, (v0, v1), ids[0]), wall("u", u1, -1, (v0, v1), ids[1]),
            wall("v", v0, +1, (u0, u1), ids[2]), wall("v", v1, -1, (u0, u1), ids[3])]


def lengths(edges):
    return sorted(round(e.length, 3) for e in edges)


def test_rectangle_edges_sit_exactly_on_walls():
    corners, edges = arrangement_polygon(region((1, 1, 5, 4)), FRAME, box_walls(1, 1, 5, 4))
    assert len(edges) == 4
    assert lengths(edges) == pytest.approx([3.0, 3.0, 4.0, 4.0], abs=1e-9)
    assert polygon_area(corners) == pytest.approx(12.0, abs=1e-9)
    assert all(e.coverage == pytest.approx(1.0) and e.wall_ids for e in edges)


def test_l_shaped_room():
    # 4 x 4 square with a 2 x 2 bite out of the top-right corner
    walls = [wall("u", 1, +1, (1, 5), 0), wall("v", 1, +1, (1, 5), 1),
             wall("u", 5, -1, (1, 3), 2), wall("v", 3, -1, (3, 5), 3),
             wall("u", 3, -1, (3, 5), 4), wall("v", 5, -1, (1, 3), 5)]
    corners, edges = arrangement_polygon(region((1, 1, 5, 3), (1, 3, 3, 5)), FRAME, walls)
    assert len(edges) == 6
    assert polygon_area(corners) == pytest.approx(12.0, abs=1e-9)
    assert lengths(edges) == pytest.approx([2.0, 2.0, 2.0, 2.0, 4.0, 4.0], abs=1e-9)


def test_furniture_hole_and_doorway_notch_do_not_change_the_room():
    mask = region((1, 1, 5, 4))
    mask[40:60, 40:60] = False  # a 1 m x 1 m bed hiding the floor
    mask[30:46, 98:102] = True  # region pokes into a doorway on the right wall
    corners, edges = arrangement_polygon(mask, FRAME, box_walls(1, 1, 5, 4))
    assert polygon_area(corners) == pytest.approx(12.0, abs=1e-9)
    assert len(edges) == 4


def test_noise_lines_from_furniture_do_not_split_the_room():
    walls = box_walls(1, 1, 5, 4) + [wall("u", 3.0, -1, (1.5, 2.0), 9)]  # a wardrobe face mid-room
    corners, edges = arrangement_polygon(region((1, 1, 5, 4)), FRAME, walls)
    assert polygon_area(corners) == pytest.approx(12.0, abs=1e-9)
    assert len(edges) == 4


def test_missing_wall_falls_back_to_an_inferred_edge():
    walls = box_walls(1, 1, 5, 4)[:3]  # the v = 4 wall was never seen
    mask = region((1, 1, 5, 4))
    rows, cols = np.nonzero(mask)
    bounds = (cols.min() * CELL, (cols.max() + 1) * CELL, rows.min() * CELL, (rows.max() + 1) * CELL)
    walls += fallback_lines(bounds, walls, PolygonConfig().fallback_gap_m)
    corners, edges = arrangement_polygon(mask, FRAME, walls)
    inferred = [e for e in edges if not e.wall_ids]
    assert len(inferred) == 1
    assert inferred[0].sigma == INFERRED_SIGMA_M
    assert inferred[0].coord == pytest.approx(3.95, abs=0.01)  # region extent, 5 cm short of the true wall
    assert polygon_area(corners) == pytest.approx(4.0 * 2.95, abs=0.01)


def test_collinear_wall_pieces_merge_into_one_edge():
    walls = box_walls(1, 1, 5, 4)
    walls[0] = wall("u", 1.0, +1, (1, 2.4), 0)
    walls.append(wall("u", 1.01, +1, (2.6, 4), 7))  # same wall, two pieces either side of a gap
    corners, edges = arrangement_polygon(region((1, 1, 5, 4)), FRAME, walls)
    left = [e for e in edges if e.axis == "u" and e.coord < 2][0]
    assert left.wall_ids == [0, 7]
    assert left.coverage == pytest.approx((1.4 + 1.4) / 3.0, abs=0.01)


def test_not_enough_lines_is_an_error():
    with pytest.raises(ValueError, match="two lines"):
        arrangement_polygon(region((1, 1, 5, 4)), FRAME, box_walls(1, 1, 5, 4)[:2])


def test_end_to_end_rotated_room():
    """C7a planes -> C6 rooms -> C7b measurement on a 4.0 x 3.0 m room (2.6 m ceiling) rotated 20 deg."""
    from scan.measure import measure_rooms
    from scan.rooms import segment_rooms
    from scan.structure import StructureModel
    from scan.structure.planes import StructureConfig, StructureStats, find_ceilings, find_floor, find_walls
    from tests.test_structure import ROT_DEG, box_room

    grid, cfg = box_room(), StructureConfig()
    floor = find_floor(grid, None, cfg)
    walls = find_walls(grid, ROT_DEG, cfg, StructureStats())
    model = StructureModel("synthetic", 0.02, len(grid), grid.label_counts(), ROT_DEG, floor,
                           find_ceilings(grid, floor, cfg), None, walls, StructureStats(), grid=grid)
    layout = segment_rooms(model, trajectory=None)
    assert len(layout.rooms) == 1
    rooms, _ = measure_rooms(model, layout)
    room = rooms[0]
    assert sorted(w.length_m for w in room.walls) == pytest.approx([3.0, 3.0, 4.0, 4.0], abs=0.01)
    assert room.floor_area_m2 == pytest.approx(12.0, abs=0.05)
    assert room.ceiling_height_m == pytest.approx(2.6, abs=0.005)
    assert room.n_inferred == 0
