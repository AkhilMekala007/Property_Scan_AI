"""2D floor map in the room-aligned frame: where is inside, where are walls and doors.

The map is rotated by the dominant wall direction so walls run along the grid
axes, which keeps room outlines clean and axis-aligned.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from scan.core.types import Trajectory
from scan.semantics.classes import Surface
from scan.structure import StructureModel
from scan.structure.voxels import UNKNOWN


@dataclass(frozen=True)
class FloorMapConfig:
    cell_m: float = 0.05
    band_low_m: float = 0.3  # wall evidence is taken between these heights above the floor
    band_high_m: float = 1.9
    floor_tol_m: float = 0.05
    path_radius_m: float = 0.25  # the walked path is free space
    min_wall_len_m: float = 0.5  # fitted wall segments shorter than this are not drawn as barriers
    seal_gap_m: float = 0.3  # gaps in wall evidence up to this wide are sealed; doorways stay open
    margin_m: float = 0.5


@dataclass
class GridFrame:
    """Room-aligned 2D frame: u/v are xz rotated by -manhattan, then gridded."""

    angle_deg: float
    origin_uv: tuple[float, float]
    cell_m: float
    shape: tuple[int, int]  # (rows = v, cols = u)

    def _rot(self) -> np.ndarray:
        th = np.radians(self.angle_deg)
        return np.array([[np.cos(th), np.sin(th)], [-np.sin(th), np.cos(th)]])

    def xz_to_uv(self, xz: np.ndarray) -> np.ndarray:
        return np.asarray(xz, float) @ self._rot().T

    def uv_to_xz(self, uv: np.ndarray) -> np.ndarray:
        return np.asarray(uv, float) @ self._rot()

    def xz_to_cell(self, xz: np.ndarray) -> np.ndarray:
        """(N, 2) world xz -> (N, 2) integer (row, col)."""
        uv = self.xz_to_uv(np.atleast_2d(xz))
        col = np.floor((uv[:, 0] - self.origin_uv[0]) / self.cell_m).astype(int)
        row = np.floor((uv[:, 1] - self.origin_uv[1]) / self.cell_m).astype(int)
        return np.column_stack([row, col])

    def cell_to_xz(self, rc: np.ndarray) -> np.ndarray:
        rc = np.atleast_2d(rc).astype(float)
        uv = np.column_stack([
            self.origin_uv[0] + (rc[:, 1] + 0.5) * self.cell_m,
            self.origin_uv[1] + (rc[:, 0] + 0.5) * self.cell_m,
        ])
        return self.uv_to_xz(uv)

    def in_bounds(self, rc: np.ndarray) -> np.ndarray:
        return (rc[:, 0] >= 0) & (rc[:, 0] < self.shape[0]) & (rc[:, 1] >= 0) & (rc[:, 1] < self.shape[1])


@dataclass
class FloorMap:
    frame: GridFrame
    inside: np.ndarray  # bool: floor seen, walked, or enclosed furniture footprint
    barrier: np.ndarray  # bool: wall evidence
    door: np.ndarray  # bool: door-labelled voxels at body height
    floor_seen: np.ndarray  # bool
    ceiling_y: np.ndarray  # float: median height of ceiling voxels per cell, NaN if none


def _mark(mask: np.ndarray, frame: GridFrame, xz: np.ndarray) -> None:
    rc = frame.xz_to_cell(xz)
    rc = rc[frame.in_bounds(rc)]
    mask[rc[:, 0], rc[:, 1]] = True


def _disk(radius_cells: int) -> np.ndarray:
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius_cells + 1, 2 * radius_cells + 1))


def _fill_enclosed(inside: np.ndarray, barrier: np.ndarray) -> np.ndarray:
    """Fill holes that can't reach the map border without crossing a wall (e.g. under furniture)."""
    outside = (~inside & ~barrier).astype(np.uint8)
    h, w = outside.shape
    padded = np.pad(outside, 1, constant_values=1)
    mask = np.zeros((h + 4, w + 4), np.uint8)
    cv2.floodFill(padded, mask, (0, 0), 2)
    reachable = padded[1:-1, 1:-1] == 2
    return inside | (~reachable & ~barrier)


def build_floor_map(model: StructureModel, trajectory: Trajectory | None,
                    cfg: FloorMapConfig | None = None) -> FloorMap:
    cfg = cfg or FloorMapConfig()
    grid, floor = model.grid, model.floor
    if grid is None or floor is None:
        raise ValueError("structure model needs a voxel grid and a floor plane")

    pts = grid.centers
    above = pts[:, 1] - (floor.a * pts[:, 0] + floor.b * pts[:, 2] + floor.c)
    ny = np.abs(grid.normals[:, 1])

    frame0 = GridFrame(model.manhattan_deg, (0.0, 0.0), cfg.cell_m, (1, 1))
    uv = frame0.xz_to_uv(pts[:, [0, 2]])
    keep = (above > -0.2) & (above < 3.5)
    lo = np.percentile(uv[keep], 0.2, axis=0) - cfg.margin_m
    hi = np.percentile(uv[keep], 99.8, axis=0) + cfg.margin_m
    shape = (int((hi[1] - lo[1]) / cfg.cell_m) + 1, int((hi[0] - lo[0]) / cfg.cell_m) + 1)
    frame = GridFrame(model.manhattan_deg, (float(lo[0]), float(lo[1])), cfg.cell_m, shape)

    floor_seen = np.zeros(shape, bool)
    barrier = np.zeros(shape, bool)
    door = np.zeros(shape, bool)
    xz = pts[:, [0, 2]]

    is_floor = (ny > 0.95) & (np.abs(above) < cfg.floor_tol_m) & np.isin(grid.labels, [Surface.FLOOR, UNKNOWN])
    _mark(floor_seen, frame, xz[is_floor])
    band = (above > cfg.band_low_m) & (above < cfg.band_high_m)
    is_wall = band & (ny < 0.3) & np.isin(grid.labels, [Surface.WALL, Surface.WINDOW])
    _mark(barrier, frame, xz[is_wall])
    is_door = (above > cfg.band_low_m) & (above < 2.0) & (grid.labels == Surface.DOOR)
    _mark(door, frame, xz[is_door])

    # Fitted wall segments seal small gaps in the raw evidence (furniture hiding a wall), but
    # only near body-height evidence: wall planes include the lintel above doors, so drawing
    # whole segments would seal every doorway.
    lines = np.zeros(shape, np.uint8)
    for wall in model.walls:
        if wall.length_seen_m < cfg.min_wall_len_m:
            continue
        a, b = frame.xz_to_cell(np.array([wall.start_xz, wall.end_xz]))
        cv2.line(lines, (int(a[1]), int(a[0])), (int(b[1]), int(b[0])), 1, 1)
    reach = max(1, int(round(cfg.seal_gap_m / 2 / cfg.cell_m)))
    near_evidence = cv2.dilate(barrier.astype(np.uint8), _disk(reach)).astype(bool)
    barrier |= lines.astype(bool) & near_evidence

    # Ceiling height per cell, used later to give each room its own ceiling level.
    ceiling_y = np.full(shape, np.nan, np.float32)
    is_ceiling = (ny > 0.95) & (above > 2.0) & np.isin(grid.labels, [Surface.CEILING, UNKNOWN])
    if is_ceiling.any():
        rc = frame.xz_to_cell(xz[is_ceiling])
        ok = frame.in_bounds(rc)
        rc, ys = rc[ok], pts[is_ceiling, 1][ok]
        flat = rc[:, 0] * shape[1] + rc[:, 1]
        order = np.argsort(flat)
        flat, ys = flat[order], ys[order]
        starts = np.r_[0, np.flatnonzero(np.diff(flat)) + 1]
        for s, e in zip(starts, np.r_[starts[1:], len(flat)]):
            ceiling_y.flat[flat[s]] = np.median(ys[s:e])

    # Extra inside evidence where furniture hides the floor: furniture tops (beds, tables)
    # and ceiling seen overhead are both inside a room by definition.
    furniture_top = np.zeros(shape, bool)
    is_top = (ny > 0.9) & (above > 0.2) & (above < 1.5) & (grid.labels == Surface.OTHER)
    _mark(furniture_top, frame, xz[is_top])
    overhead = np.isfinite(ceiling_y)

    inside = cv2.dilate((floor_seen | furniture_top | overhead).astype(np.uint8), _disk(1)).astype(bool)
    if trajectory is not None and len(trajectory):
        path = np.zeros(shape, np.uint8)
        _mark(path.view(bool), frame, trajectory.positions[:, [0, 2]])
        r = max(1, int(round(cfg.path_radius_m / cfg.cell_m)))
        inside |= cv2.dilate(path, _disk(r)).astype(bool)
    inside = cv2.morphologyEx(inside.astype(np.uint8), cv2.MORPH_CLOSE, _disk(2)).astype(bool)
    inside &= ~barrier
    inside = _fill_enclosed(inside, barrier)
    return FloorMap(frame, inside, barrier, door, floor_seen, ceiling_y)
