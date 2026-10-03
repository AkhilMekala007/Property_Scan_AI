"""Wall elevation images: a front view of one wall (along-wall x height) with evidence layers.

- solid:   wall surface observed on the plane (fused voxels)
- through: camera rays from inside the room that pass through the plane and hit
           something beyond it - direct proof of an opening at the crossing point
- door / window: door- or window-labelled voxels near the plane

"Through" must come from rays, not voxels: the fused model also contains the
neighbouring room (the camera walked there), which sits behind every partition
wall and would make whole walls look open.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from scan.core.geometry import backproject, transform_points
from scan.core.types import Frame
from scan.rooms.floormap import GridFrame
from scan.semantics.classes import Surface
from scan.structure import StructureModel


@dataclass(frozen=True)
class ElevationConfig:
    res_m: float = 0.02
    near_m: float = 0.05  # |distance to plane| for solid wall evidence
    through_m: float = 0.08  # a hit this far beyond the plane counts as seen-through
    label_band: tuple[float, float] = (-0.10, 0.25)  # door/window labels: plane distance range (curtains hang in front)
    depth_stride: int = 2
    max_frames: int = 120


@dataclass
class WallSpec:
    """One room edge in the room grid frame."""

    axis: str  # "u" (u = coord) or "v" (v = coord)
    coord: float
    sign: int  # +1: room on the side of increasing coord
    s0: float  # along-wall extent (absolute u or v), s0 < s1
    s1: float
    height: float  # how high to look (ceiling height, or a default)


@dataclass
class Elevation:
    spec: WallSpec
    res: float
    solid: np.ndarray  # (rows = height bins from the floor up, cols = along-wall bins)
    through: np.ndarray  # counts
    door: np.ndarray
    window: np.ndarray
    # solid voxel samples kept for sub-cell jamb / head refinement
    solid_s: np.ndarray
    solid_h: np.ndarray
    # exact plane crossings of rays that passed through (for clear-opening edges)
    cross_s: np.ndarray
    cross_h: np.ndarray

    @property
    def shape(self) -> tuple[int, int]:
        return self.solid.shape

    def s_of_col(self, col: float) -> float:
        return self.spec.s0 + col * self.res

    def h_of_row(self, row: float) -> float:
        return row * self.res


def _plane_coords(spec: WallSpec, uv: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(distance into the room, along-wall coordinate) for points in uv."""
    i = 0 if spec.axis == "u" else 1
    return spec.sign * (uv[:, i] - spec.coord), uv[:, 1 - i]


def _bins(spec: WallSpec, res: float, s: np.ndarray, h: np.ndarray):
    n_cols = int(np.ceil((spec.s1 - spec.s0) / res)) + 1
    n_rows = int(np.ceil(spec.height / res)) + 1
    col = np.floor((s - spec.s0) / res).astype(int)
    row = np.floor(h / res).astype(int)
    ok = (col >= 0) & (col < n_cols) & (row >= 0) & (row < n_rows)
    return (n_rows, n_cols), row, col, ok


def build_elevation(spec: WallSpec, model: StructureModel, frame2d: GridFrame,
                    frames: list[Frame], cfg: ElevationConfig) -> Elevation:
    grid, floor = model.grid, model.floor
    p = grid.centers
    uv = frame2d.xz_to_uv(p[:, [0, 2]])
    d, s = _plane_coords(spec, uv)
    h = p[:, 1] - (floor.a * p[:, 0] + floor.b * p[:, 2] + floor.c)
    shape, row, col, ok = _bins(spec, cfg.res_m, s, h)

    solid = np.zeros(shape, bool)
    door = np.zeros(shape, np.int32)
    window = np.zeros(shape, np.int32)
    labels = grid.labels
    vertical = np.abs(grid.normals[:, 1]) < 0.5
    is_solid = ok & (np.abs(d) < cfg.near_m) & vertical & ~np.isin(
        labels, [Surface.DOOR, Surface.WINDOW, Surface.PERSON, Surface.MIRROR])
    solid[row[is_solid], col[is_solid]] = True
    band = ok & (d > cfg.label_band[0]) & (d < cfg.label_band[1])
    np.add.at(door, (row[band & (labels == Surface.DOOR)], col[band & (labels == Surface.DOOR)]), 1)
    np.add.at(window, (row[band & (labels == Surface.WINDOW)], col[band & (labels == Surface.WINDOW)]), 1)

    through = np.zeros(shape, np.int32)
    cross_s, cross_h = [], []
    for fr in frames[: cfg.max_frames]:
        cam_uv = frame2d.xz_to_uv(fr.T_world_cam[[0, 2], 3][None])
        d_cam, _ = _plane_coords(spec, cam_uv)
        d_cam = float(d_cam[0])
        if d_cam <= 0.05:
            continue
        depth = fr.depth()
        conf = fr.confidence()
        pts = backproject(depth, fr.depth_intrinsics, mask=(conf == 2) if conf is not None else None,
                          stride=cfg.depth_stride)
        pw = transform_points(fr.T_world_cam, pts)
        dp, _ = _plane_coords(spec, frame2d.xz_to_uv(pw[:, [0, 2]]))
        beyond = dp < -cfg.through_m
        if not beyond.any():
            continue
        cam = fr.T_world_cam[:3, 3]
        q = pw[beyond]
        t = d_cam / (d_cam - dp[beyond])  # fraction of the ray where it crosses the plane
        x = cam + t[:, None] * (q - cam)
        _, sx = _plane_coords(spec, frame2d.xz_to_uv(x[:, [0, 2]]))
        hx = x[:, 1] - (floor.a * x[:, 0] + floor.b * x[:, 2] + floor.c)
        _, r2, c2, ok2 = _bins(spec, cfg.res_m, sx, hx)
        np.add.at(through, (r2[ok2], c2[ok2]), 1)
        cross_s.append(sx[ok2].astype(np.float32))
        cross_h.append(hx[ok2].astype(np.float32))

    cs = np.concatenate(cross_s) if cross_s else np.zeros(0, np.float32)
    ch = np.concatenate(cross_h) if cross_h else np.zeros(0, np.float32)
    return Elevation(spec, cfg.res_m, solid, through, door, window, s[is_solid], h[is_solid], cs, ch)


def render_elevation(el: Elevation, out_path, boxes=()) -> None:
    """Debug: grey = solid, blue = seen-through, red = door label, purple = window label; boxes = openings."""
    rows, cols = el.shape
    img = np.full((rows, cols, 3), 255, np.uint8)
    img[el.window > 0] = (200, 120, 200)
    img[el.door > 0] = (80, 80, 220)
    img[el.through > 0] = (230, 160, 60)
    img[el.solid] = (110, 110, 110)
    img = img[::-1]  # floor at the bottom
    img = cv2.resize(img, (cols * 3, rows * 3), interpolation=cv2.INTER_NEAREST)
    for (c0, r0, c1, r1, colour) in boxes:
        cv2.rectangle(img, (int(c0 * 3), int((rows - r1) * 3)), (int(c1 * 3), int((rows - r0) * 3)), colour, 2)
    cv2.imwrite(str(out_path), img)
