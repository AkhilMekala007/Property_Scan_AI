"""From a detection box to a measured region on a room surface.

1. Refine the box to the pixels that actually differ from the surrounding surface (Lab colour
   distance to the surface just outside the box), restricted to wall/floor/ceiling pixels.
2. Back-project those pixels with depth, assign them to the room surface they lie on, and
   measure their footprint on that plane on a 1 cm grid.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from scan.core.geometry import transform_points
from scan.core.types import Frame
from scan.measure import RoomMeasurement
from scan.rooms.floormap import GridFrame
from scan.semantics.classes import Surface
from scan.structure import StructureModel

SURFACES = (Surface.WALL, Surface.FLOOR, Surface.CEILING)
GRID_M = 0.01


@dataclass(frozen=True)
class MeasureConfig:
    ring: float = 0.35  # reference ring around the box, as a fraction of box size
    min_delta_e: float = 10.0  # minimum Lab distance from the surface to count as damage
    mad_k: float = 3.0
    min_mask_frac: float = 0.03
    plane_tol_m: float = 0.06
    min_surface_agree: float = 0.6
    pixel_stride: int = 2
    edge_band_px: int = 12  # pixels this close to a surface boundary are 'on an edge'
    max_edge_frac: float = 0.5  # a mask mostly along a surface boundary is a corner line


@dataclass
class SurfaceRef:
    id: str  # e.g. "room_2.wall_3", "room_2.ceiling"
    room_id: int
    room_name: str
    kind: str  # wall | floor | ceiling
    wall_index: int | None


@dataclass
class ViewMeasurement:
    frame_index: int
    cls: str
    score: float
    surface: SurfaceRef
    area_m2: float
    area_sigma_m2: float
    centroid_2d: tuple[float, float]  # surface coords: (along, height) for walls, (u, v) for floor/ceiling
    extent_2d: tuple[float, float]  # bounding size on the surface
    length_m: float  # longest extent (crack length)
    bottom_m: float | None  # walls: lowest point above the floor
    outline_2d: list[tuple[float, float]]
    cells: set  # occupied 1 cm cells, for fusing views


def refine_mask(rgb: np.ndarray, labels_full: np.ndarray, box, cfg: MeasureConfig):
    """Boolean mask (full image) of pixels inside the box that differ from the surrounding surface;
    None if nothing stands out, "edge" if the difference runs along a surface boundary."""
    h, w = labels_full.shape
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    bw, bh = x1 - x0, y1 - y0
    rx0, ry0 = max(0, int(x0 - cfg.ring * bw)), max(0, int(y0 - cfg.ring * bh))
    rx1, ry1 = min(w, int(x1 + cfg.ring * bw)), min(h, int(y1 + cfg.ring * bh))
    surface = np.isin(labels_full, SURFACES)
    lab = cv2.cvtColor(rgb[ry0:ry1, rx0:rx1], cv2.COLOR_RGB2LAB).astype(np.float32)
    in_box = np.zeros(lab.shape[:2], bool)
    in_box[y0 - ry0:y1 - ry0, x0 - rx0:x1 - rx0] = True
    surf = surface[ry0:ry1, rx0:rx1]
    ring = surf & ~in_box
    if ring.sum() < 50:
        return None
    ref = np.median(lab[ring], axis=0)
    dist = np.linalg.norm(lab - ref, axis=-1)
    mad = float(np.median(np.abs(dist[ring] - np.median(dist[ring])))) * 1.4826
    thr = max(cfg.min_delta_e, float(np.median(dist[ring])) + cfg.mad_k * mad)
    m = (dist > thr) & in_box & surf
    m = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)).astype(bool)
    if m.sum() < cfg.min_mask_frac * bw * bh:
        return None
    # Corner lines (wall meets ceiling, wall meets wall or furniture) look like long cracks.
    # Reject masks that lie mostly along a boundary between differently labelled surfaces.
    crop_labels = labels_full[ry0:ry1, rx0:rx1].astype(np.uint8)
    boundary = (cv2.morphologyEx(crop_labels, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0).astype(np.uint8)
    near_edge = cv2.dilate(boundary, np.ones((2 * cfg.edge_band_px + 1,) * 2, np.uint8)).astype(bool)
    if (m & near_edge).sum() > cfg.max_edge_frac * m.sum():
        return "edge"
    out = np.zeros((h, w), bool)
    out[ry0:ry1, rx0:rx1] = m
    return out


def _surfaces_of(rooms: list[RoomMeasurement], model: StructureModel, frame2d: GridFrame):
    """Plane descriptions of every room surface in the room grid frame."""
    out = []
    for r in rooms:
        poly = np.array(r.corners_uv)
        centre = poly.mean(axis=0)
        for k in range(len(poly)):
            a, b = poly[k], poly[(k + 1) % len(poly)]
            axis = 0 if abs(a[0] - b[0]) < 1e-9 else 1
            sign = 1 if centre[axis] > a[axis] else -1
            out.append(("wall", r, k, axis, float(a[axis]), sign, (min(a[1 - axis], b[1 - axis]), max(a[1 - axis], b[1 - axis]))))
        out.append(("floor", r, None, poly))
        if r.ceiling_height_m is not None:
            out.append(("ceiling", r, None, poly))
    return out


def project_mask(frame: Frame, mask: np.ndarray, rooms: list[RoomMeasurement], model: StructureModel,
                 frame2d: GridFrame, cfg: MeasureConfig):
    """Mask pixels -> world points -> (SurfaceRef, 2D surface coords) or None."""
    depth = frame.depth()
    if depth is None or frame.T_world_cam is None:
        return None
    vv, uu = np.nonzero(mask[::cfg.pixel_stride, ::cfg.pixel_stride])
    uu, vv = uu * cfg.pixel_stride, vv * cfg.pixel_stride
    K, dK = frame.intrinsics, frame.depth_intrinsics
    du = np.clip(((uu + 0.5) * dK.width / K.width - 0.5).round().astype(int), 0, dK.width - 1)
    dv = np.clip(((vv + 0.5) * dK.height / K.height - 0.5).round().astype(int), 0, dK.height - 1)
    z = depth[dv, du]
    ok = np.isfinite(z) & (z > 0)
    if ok.sum() < 20:
        return None
    pc = np.stack([(uu[ok] - K.cx) * z[ok] / K.fx, (vv[ok] - K.cy) * z[ok] / K.fy, z[ok]], axis=1)
    pw = transform_points(frame.T_world_cam, pc)
    uv = frame2d.xz_to_uv(pw[:, [0, 2]])
    f = model.floor
    h = pw[:, 1] - (f.a * pw[:, 0] + f.b * pw[:, 2] + f.c)

    best, best_hits, best_coords = None, 0, None
    for s in _surfaces_of(rooms, model, frame2d):
        kind, room = s[0], s[1]
        if kind == "wall":
            _, _, k, axis, coord, sign, (lo, hi) = s
            hit = (np.abs(uv[:, axis] - coord) < cfg.plane_tol_m) & (uv[:, 1 - axis] > lo - 0.05) & \
                  (uv[:, 1 - axis] < hi + 0.05) & (h > -0.05)
            coords = np.column_stack([uv[:, 1 - axis], h])
            ref = SurfaceRef(f"{room.name}.wall_{k}", room.id, room.name, "wall", k)
        else:
            poly = s[3].astype(np.float32)
            inside = np.array([cv2.pointPolygonTest(poly, (float(a), float(b)), False) >= 0 for a, b in uv])
            target = 0.0 if kind == "floor" else room.ceiling_height_m
            hit = inside & (np.abs(h - target) < cfg.plane_tol_m)
            coords = uv.copy()
            ref = SurfaceRef(f"{room.name}.{kind}", room.id, room.name, kind, None)
        n = int(hit.sum())
        if n > best_hits:
            best, best_hits, best_coords = ref, n, coords[hit]
    if best is None or best_hits < cfg.min_surface_agree * len(uv):
        return None
    spacing = float(np.median(z[ok])) * cfg.pixel_stride / K.fx  # distance between sampled points
    return best, best_coords, spacing


def footprint(coords: np.ndarray, spacing_m: float = 0.0):
    """Area, sigma, centroid, extent, length, outline, cells and bottom of 2D surface points.

    Points are rasterised on a 1 cm grid; gaps up to the point spacing are closed so a sparse
    sampling of a region still measures its full area (a 0.49 m2 patch sampled every 3.5 cm read
    0.16 m2 before). The grid is padded so closing never grows into the border.
    """
    pad = 2 + int(np.ceil(spacing_m / GRID_M))
    cells = np.unique(np.floor(coords / GRID_M).astype(np.int64), axis=0)
    lo = cells.min(axis=0) - pad
    grid = np.zeros(tuple((cells.max(axis=0) - lo + pad + 1)[::-1]), np.uint8)
    grid[cells[:, 1] - lo[1], cells[:, 0] - lo[0]] = 1
    r = max(1, int(np.ceil(spacing_m / GRID_M)))
    grid = cv2.morphologyEx(grid, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (2 * r + 1, 2 * r + 1)))
    contours, _ = cv2.findContours(grid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(grid)
    cv2.drawContours(filled, contours, -1, 1, -1)
    area = float(filled.sum()) * GRID_M ** 2
    k = np.ones((3, 3), np.uint8)
    sigma = float(cv2.dilate(filled, k).sum() - cv2.erode(filled, k).sum()) * GRID_M ** 2 / 2
    ys, xs = np.nonzero(filled)
    pts = np.column_stack([xs + lo[0] + 0.5, ys + lo[1] + 0.5]) * GRID_M
    extent = pts.max(axis=0) - pts.min(axis=0) + GRID_M
    biggest = max(contours, key=cv2.contourArea)
    outline = (biggest[:, 0, :] + lo + 0.5) * GRID_M
    occupied = {(int(x), int(y)) for x, y in zip(xs + lo[0], ys + lo[1])}
    return area, sigma, tuple(pts.mean(axis=0)), tuple(extent), float(np.hypot(*extent)),         [tuple(map(float, p)) for p in outline], occupied, float(pts[:, 1].min()) - GRID_M / 2
