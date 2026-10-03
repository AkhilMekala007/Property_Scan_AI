"""Floor, ceiling and wall planes from the fused voxel grid.

All fits are deterministic: histograms to find candidates, then trimmed
weighted least squares to refine them. World +y is up (gravity aligned).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from scan.semantics.classes import Surface
from scan.structure.voxels import UNKNOWN, VoxelGrid

HORIZONTAL_NY = 0.95  # |n_y| above this: horizontal surface
VERTICAL_NY = 0.2  # |n_y| below this: vertical surface
AREA_CELL = 0.10  # area is counted on a 10 cm grid


@dataclass
class HorizontalPlane:
    """y = a*x + b*z + c, fitted on horizontal voxels."""

    a: float
    b: float
    c: float
    centroid_xz: tuple[float, float]
    y_at_centroid: float
    sigma_m: float  # one-sigma uncertainty of the height at the centroid
    rms_m: float  # residual spread of the inliers
    tilt_deg: float
    n_voxels: int
    area_m2: float

    def y_at(self, x: float, z: float) -> float:
        return self.a * x + self.b * z + self.c


@dataclass
class WallPlane:
    """Vertical plane n·p = offset (n horizontal, pointing into the room), one contiguous segment."""

    id: int
    normal_xz: tuple[float, float]
    angle_deg: float  # direction of the normal in the xz plane
    snapped: bool  # angle snapped to the dominant (Manhattan) directions
    offset_m: float
    sigma_m: float
    rms_m: float
    start_xz: tuple[float, float]
    end_xz: tuple[float, float]
    length_seen_m: float
    y_min: float
    y_max: float
    area_seen_m2: float
    n_voxels: int

    @property
    def coverage(self) -> float:
        """Share of the segment's rectangle actually observed."""
        box = self.length_seen_m * max(self.y_max - self.y_min, 1e-6)
        return min(1.0, self.area_seen_m2 / box) if box > 0 else 0.0


@dataclass
class StructureConfig:
    family_min_share: float = 0.03  # a wall direction needs this share of wall voxels
    snap_deg: float = 3.0
    family_halfwidth_deg: float = 10.0
    wall_bin_m: float = 0.01
    wall_inlier_m: float = 0.04
    wall_min_voxels: int = 400  # ~0.16 m2 of wall at 2 cm voxels
    wall_nms_m: float = 0.06
    deep_window_m: float = 0.08  # a strong surface up to this far behind a peak is the real wall
    deep_ratio: float = 0.4
    segment_gap_m: float = 0.4
    segment_min_len_m: float = 0.3
    segment_min_voxels: int = 150
    floor_window_m: float = 0.2
    ceiling_min_above_floor_m: float = 2.0
    ceiling_min_area_m2: float = 1.0


@dataclass
class StructureStats:
    n_wall_voxels: int = 0
    n_wall_voxels_assigned: int = 0
    n_mirror_voxels: int = 0
    n_person_voxels: int = 0
    families_deg: list[float] = field(default_factory=list)


# ---------- helpers ----------

def _area(points_xz: np.ndarray) -> float:
    if len(points_xz) == 0:
        return 0.0
    cells = np.unique(np.floor(points_xz / AREA_CELL).astype(np.int64), axis=0)
    return float(len(cells) * AREA_CELL * AREA_CELL)


def _smooth_circular(hist: np.ndarray, sigma_bins: float) -> np.ndarray:
    radius = int(3 * sigma_bins) + 1
    kernel = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma_bins) ** 2)
    padded = np.concatenate([hist[-radius:], hist, hist[:radius]])
    return np.convolve(padded, kernel / kernel.sum(), mode="valid")


def _smooth(hist: np.ndarray, sigma_bins: float) -> np.ndarray:
    radius = int(3 * sigma_bins) + 1
    kernel = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma_bins) ** 2)
    return np.convolve(hist, kernel / kernel.sum(), mode="same")


def _peaks(values: np.ndarray, min_value: float, min_sep: int) -> list[int]:
    """Greedy non-maximum suppression on a 1D signal."""
    order = np.argsort(values)[::-1]
    chosen: list[int] = []
    for i in order:
        if values[i] < min_value:
            break
        if all(abs(i - j) >= min_sep for j in chosen):
            chosen.append(int(i))
    return chosen


def _trimmed_mean(x: np.ndarray, w: np.ndarray, iters: int = 3, k: float = 2.5) -> tuple[float, float, np.ndarray]:
    mask = np.ones(len(x), bool)
    mean = float(np.average(x, weights=w))
    for _ in range(iters):
        mean = float(np.average(x[mask], weights=w[mask]))
        rms = float(np.sqrt(np.average((x[mask] - mean) ** 2, weights=w[mask])))
        new = np.abs(x - mean) <= max(k * rms, 1e-4)
        if new.sum() < 3 or np.array_equal(new, mask):
            break
        mask = new
    rms = float(np.sqrt(np.average((x[mask] - mean) ** 2, weights=w[mask])))
    return mean, rms, mask


# ---------- dominant directions ----------

def manhattan_angle(normals: np.ndarray, weights: np.ndarray) -> float:
    """Dominant wall direction in degrees, modulo 90 (in [0, 90))."""
    phi = np.arctan2(normals[:, 2], normals[:, 0])
    z = np.sum(weights * np.exp(4j * phi))
    return float(np.degrees(np.angle(z) / 4) % 90)


def wall_families(normals: np.ndarray, weights: np.ndarray, manhattan_deg: float,
                  cfg: StructureConfig) -> list[tuple[float, bool]]:
    """Normal directions (deg, in [0, 360)) that carry a meaningful share of wall voxels."""
    phi = np.degrees(np.arctan2(normals[:, 2], normals[:, 0])) % 360
    hist, _ = np.histogram(phi, bins=360, range=(0, 360), weights=weights)
    smooth = _smooth_circular(hist, 1.5)
    total = weights.sum()
    families = []
    for b in _peaks(smooth, cfg.family_min_share * total / 12, min_sep=15):
        centre = b + 0.5
        near = np.abs((phi - centre + 180) % 360 - 180) <= 5
        if weights[near].sum() < cfg.family_min_share * total:
            continue
        ang = np.degrees(np.angle(np.sum(weights[near] * np.exp(1j * np.radians(phi[near]))))) % 360
        snapped_to = manhattan_deg + 90 * np.round((ang - manhattan_deg) / 90)
        snapped = abs(((ang - snapped_to) + 180) % 360 - 180) <= cfg.snap_deg
        families.append((float(snapped_to % 360 if snapped else ang), bool(snapped)))
    # merge duplicates produced by snapping
    unique: dict[int, tuple[float, bool]] = {}
    for ang, snapped in families:
        unique.setdefault(int(round(ang * 10)), (ang, snapped))
    return sorted(unique.values())


# ---------- horizontal planes ----------

def fit_horizontal(points: np.ndarray, weights: np.ndarray) -> HorizontalPlane | None:
    if len(points) < 50:
        return None
    x, y, z = points[:, 0], points[:, 1], points[:, 2]
    mask = np.ones(len(points), bool)
    coef = np.zeros(3)
    for _ in range(4):
        A = np.column_stack([x[mask], z[mask], np.ones(mask.sum())])
        sw = np.sqrt(weights[mask])
        coef, *_ = np.linalg.lstsq(A * sw[:, None], y[mask] * sw, rcond=None)
        resid = y - (coef[0] * x + coef[1] * z + coef[2])
        rms = float(np.sqrt(np.average(resid[mask] ** 2, weights=weights[mask])))
        new = np.abs(resid) <= max(2.5 * rms, 0.003)
        if new.sum() < 50 or np.array_equal(new, mask):
            break
        mask = new
    resid = y - (coef[0] * x + coef[1] * z + coef[2])
    rms = float(np.sqrt(np.average(resid[mask] ** 2, weights=weights[mask])))
    cx, cz = float(np.average(x[mask], weights=weights[mask])), float(np.average(z[mask], weights=weights[mask]))
    tilt = float(np.degrees(np.arctan(np.hypot(coef[0], coef[1]))))
    n = int(mask.sum())
    return HorizontalPlane(
        a=float(coef[0]), b=float(coef[1]), c=float(coef[2]),
        centroid_xz=(cx, cz), y_at_centroid=float(coef[0] * cx + coef[1] * cz + coef[2]),
        sigma_m=rms / np.sqrt(n), rms_m=rms, tilt_deg=tilt, n_voxels=n,
        area_m2=_area(points[mask][:, [0, 2]]),
    )


def find_floor(grid: VoxelGrid, floor_hint_y: float | None, cfg: StructureConfig) -> HorizontalPlane | None:
    horizontal = np.abs(grid.normals[:, 1]) > HORIZONTAL_NY
    floorish = horizontal & np.isin(grid.labels, [Surface.FLOOR, UNKNOWN])
    y = grid.centers[:, 1]
    if floor_hint_y is None:
        if not floorish.any():
            return None
        hist, edges = np.histogram(y[floorish], bins=np.arange(y.min(), y.max() + 0.02, 0.02))
        floor_hint_y = float(edges[np.argmax(hist)] + 0.01)
    sel = floorish & (np.abs(y - floor_hint_y) < cfg.floor_window_m)
    return fit_horizontal(grid.centers[sel], grid.counts[sel].astype(float))


def find_ceilings(grid: VoxelGrid, floor: HorizontalPlane, cfg: StructureConfig) -> list[HorizontalPlane]:
    """Every ceiling level (main ceiling, bulkheads, lowered corridor ceilings), largest first."""
    horizontal = np.abs(grid.normals[:, 1]) > HORIZONTAL_NY
    floor_y = floor.a * grid.centers[:, 0] + floor.b * grid.centers[:, 2] + floor.c
    above = grid.centers[:, 1] - floor_y > cfg.ceiling_min_above_floor_m
    sel = horizontal & above & np.isin(grid.labels, [Surface.CEILING, UNKNOWN])
    if sel.sum() < 50:
        return []
    pts, w = grid.centers[sel], grid.counts[sel].astype(float)
    hist, edges = np.histogram(pts[:, 1], bins=np.arange(pts[:, 1].min(), pts[:, 1].max() + 0.02, 0.01), weights=w)
    smooth = _smooth(hist, 1.0)
    levels = []
    for b in _peaks(smooth, smooth.max() * 0.05, min_sep=8):
        y0 = edges[b] + 0.005
        near = np.abs(pts[:, 1] - y0) < cfg.wall_inlier_m
        if _area(pts[near][:, [0, 2]]) < cfg.ceiling_min_area_m2:
            continue
        plane = fit_horizontal(pts[near], w[near])
        if plane is not None and plane.area_m2 >= cfg.ceiling_min_area_m2:
            levels.append(plane)
    return sorted(levels, key=lambda p: -p.area_m2)


# ---------- walls ----------

def find_walls(grid: VoxelGrid, manhattan_deg: float, cfg: StructureConfig,
               stats: StructureStats) -> list[WallPlane]:
    vertical = np.abs(grid.normals[:, 1]) < VERTICAL_NY
    wall = vertical & (grid.labels == Surface.WALL)
    if wall.sum() < 2000:  # too few labelled walls: fall back to unlabelled vertical surfaces too
        wall = vertical & np.isin(grid.labels, [Surface.WALL, UNKNOWN])
    stats.n_wall_voxels = int(wall.sum())
    pts = grid.centers[wall]
    nrm = grid.normals[wall]
    w = grid.counts[wall].astype(float)
    if len(pts) == 0:
        return []

    families = wall_families(nrm, w, manhattan_deg, cfg)
    stats.families_deg = [round(a, 1) for a, _ in families]
    assigned = np.zeros(len(pts), bool)
    walls: list[WallPlane] = []
    cos_half = np.cos(np.radians(cfg.family_halfwidth_deg))

    for ang, snapped in families:
        d = np.array([np.cos(np.radians(ang)), 0.0, np.sin(np.radians(ang))])
        t = np.array([-d[2], 0.0, d[0]])
        member = (nrm @ d > cos_half) & ~assigned
        if member.sum() < cfg.wall_min_voxels:
            continue
        c = pts[member] @ d
        bins = np.arange(c.min() - cfg.wall_bin_m, c.max() + 2 * cfg.wall_bin_m, cfg.wall_bin_m)
        hist, edges = np.histogram(c, bins=bins, weights=w[member])
        smooth = _smooth(hist, 1.0)
        min_sep = int(round(cfg.wall_nms_m / cfg.wall_bin_m))
        min_peak = cfg.wall_min_voxels * np.median(w[member]) / 8
        idx_member = np.flatnonzero(member)
        for b in _peaks(smooth, min_peak, min_sep):
            b = _deepest_peak(smooth, b, int(round(cfg.deep_window_m / cfg.wall_bin_m)), cfg.deep_ratio)
            c0 = edges[b] + cfg.wall_bin_m / 2
            near = np.abs(c - c0) < cfg.wall_inlier_m
            if near.sum() < cfg.wall_min_voxels:
                continue
            idx = idx_member[near]
            idx = idx[~assigned[idx]]
            if len(idx) < cfg.wall_min_voxels:
                continue
            for seg in _segments(pts[idx] @ t, cfg):
                seg_idx = idx[seg]
                if len(seg_idx) < cfg.segment_min_voxels:
                    continue
                wall_plane = _make_wall(len(walls), pts[seg_idx], w[seg_idx], d, t, ang, snapped)
                if wall_plane.length_seen_m < cfg.segment_min_len_m:
                    continue
                walls.append(wall_plane)
                assigned[seg_idx] = True
    stats.n_wall_voxels_assigned = int(assigned.sum())
    return walls


def _deepest_peak(smooth: np.ndarray, b: int, window: int, ratio: float) -> int:
    """Among strong local maxima up to ``window`` bins deeper than peak ``b``, the deepest.

    Offsets grow into the room (the normal points into it), so a smaller bin index is deeper.
    Skirting boards, frames and furniture fronts stand in front of a wall, never behind it:
    picking the deepest strong surface finds the wall itself and stops the choice flipping
    between two similar peaks a few cm apart (a 3.5 cm jump under 5 mm of pose noise).
    """
    best = b
    for i in range(max(1, b - window), b):
        is_max = smooth[i] >= smooth[i - 1] and smooth[i] >= smooth[min(i + 1, len(smooth) - 1)]
        if is_max and smooth[i] >= ratio * smooth[b]:
            return i  # scanning from the deepest end, the first strong maximum wins
    return best


def _segments(s: np.ndarray, cfg: StructureConfig) -> list[np.ndarray]:
    """Split points along a wall line wherever there is a gap; returns index arrays."""
    order = np.argsort(s)
    gaps = np.flatnonzero(np.diff(s[order]) > cfg.segment_gap_m)
    return [order[a:b] for a, b in zip(np.r_[0, gaps + 1], np.r_[gaps + 1, len(s)])]


def _make_wall(wid: int, pts: np.ndarray, w: np.ndarray, d: np.ndarray, t: np.ndarray,
               ang: float, snapped: bool) -> WallPlane:
    offset, rms, mask = _trimmed_mean(pts @ d, w)
    inl = pts[mask]
    s = inl @ t
    s0, s1 = float(np.percentile(s, 0.5)), float(np.percentile(s, 99.5))
    base = d * offset
    start, end = base + t * s0, base + t * s1
    area = _area(np.column_stack([s, inl[:, 1]]))  # area in the wall's own (s, y) plane
    return WallPlane(
        id=wid,
        normal_xz=(float(d[0]), float(d[2])),
        angle_deg=round(float(ang), 2),
        snapped=snapped,
        offset_m=offset,
        sigma_m=rms / np.sqrt(mask.sum()),
        rms_m=rms,
        start_xz=(float(start[0]), float(start[2])),
        end_xz=(float(end[0]), float(end[2])),
        length_seen_m=s1 - s0,
        y_min=float(np.percentile(inl[:, 1], 1)),
        y_max=float(np.percentile(inl[:, 1], 99)),
        area_seen_m2=area,
        n_voxels=int(mask.sum()),
    )
