"""C7a Structure: fused labelled voxel model + floor, ceiling and wall planes of the whole capture."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

from scan.core.types import FrameSet
from scan.semantics.classes import Surface
from scan.structure.planes import (
    VERTICAL_NY,
    HorizontalPlane,
    StructureConfig,
    StructureStats,
    WallPlane,
    find_ceilings,
    find_floor,
    find_walls,
    manhattan_angle,
)
from scan.structure.voxels import VoxelGrid, fuse

__all__ = ["StructureConfig", "StructureModel", "run_structure", "write_structure", "render_structure"]


@dataclass
class StructureModel:
    capture_id: str
    voxel_size_m: float
    n_voxels: int
    label_counts: dict[str, int]
    manhattan_deg: float
    floor: HorizontalPlane | None
    ceilings: list[HorizontalPlane]  # largest first; empty when not seen
    ceiling_note: str | None
    walls: list[WallPlane]
    stats: StructureStats
    timings_s: dict[str, float] = field(default_factory=dict)
    grid: VoxelGrid | None = field(default=None, repr=False)  # kept for C6 / C7b, not serialised

    def ceiling_heights(self) -> list[tuple[float, float, float]]:
        """(height above floor, fit-only sigma, area m2) per ceiling level, at its centroid.

        The sigma reflects plane-fit noise only; depth bias and drift are added by C12 calibration.
        """
        if self.floor is None:
            return []
        out = []
        for c in self.ceilings:
            x, z = c.centroid_xz
            h = c.y_at(x, z) - self.floor.y_at(x, z)
            out.append((h, float(np.hypot(c.sigma_m, self.floor.sigma_m)), c.area_m2))
        return out

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("grid", None)
        d["ceiling_heights"] = [
            {"height_m": round(h, 4), "sigma_m": round(s, 5), "area_m2": round(a, 2)}
            for h, s, a in self.ceiling_heights()
        ]
        for w in d["walls"]:
            w["coverage"] = None
        for w, obj in zip(d["walls"], self.walls):
            w["coverage"] = round(obj.coverage, 3)
        return d


def run_structure(
    frameset: FrameSet,
    floor_hint_y: float | None = None,
    ceiling_seen: bool | None = None,
    config: StructureConfig | None = None,
) -> StructureModel:
    cfg = config or StructureConfig()
    t0 = time.perf_counter()
    grid = fuse(frameset.frames)
    t_fuse = time.perf_counter() - t0

    stats = StructureStats(
        n_mirror_voxels=int((grid.labels == Surface.MIRROR).sum()),
        n_person_voxels=int((grid.labels == Surface.PERSON).sum()),
    )
    # People move and mirrors show reflections: neither is room structure.
    grid = grid.subset(~np.isin(grid.labels, [Surface.PERSON, Surface.MIRROR]))

    vertical_wall = (np.abs(grid.normals[:, 1]) < VERTICAL_NY) & (grid.labels == Surface.WALL)
    manhattan = manhattan_angle(grid.normals[vertical_wall], grid.counts[vertical_wall].astype(float)) \
        if vertical_wall.any() else 0.0

    floor = find_floor(grid, floor_hint_y, cfg)
    if ceiling_seen is False:
        ceilings, note = [], "ceiling not observed during capture (QC)"
    elif floor is None:
        ceilings, note = [], "floor not found, so ceiling height cannot be measured"
    else:
        ceilings = find_ceilings(grid, floor, cfg)
        note = None if ceilings else "no ceiling surface large enough was found"
    walls = find_walls(grid, manhattan, cfg, stats)

    return StructureModel(
        capture_id=frameset.meta.capture_id,
        voxel_size_m=grid.voxel_size,
        n_voxels=len(grid),
        label_counts=grid.label_counts(),
        manhattan_deg=round(manhattan, 2),
        floor=floor,
        ceilings=ceilings,
        ceiling_note=note,
        walls=walls,
        stats=stats,
        timings_s={"fuse": round(t_fuse, 2), "total": round(time.perf_counter() - t0, 2)},
        grid=grid,
    )


def write_structure(model: StructureModel, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "structure.json"
    path.write_text(json.dumps(model.to_dict(), indent=2), encoding="utf-8")
    return path


_PALETTE = [(31, 119, 180), (214, 39, 40), (44, 160, 44), (148, 103, 189), (255, 127, 14),
            (23, 190, 207), (140, 86, 75), (227, 119, 194), (188, 189, 34), (127, 127, 127)]


def render_structure(model: StructureModel, out_path: Path, res: float = 0.02) -> Path:
    """Top-down: grey = wall-height voxels, coloured lines = fitted wall planes with ids."""
    grid = model.grid
    if grid is None or model.floor is None:
        raise ValueError("structure model has no voxel grid or floor")
    pts = grid.centers
    fy = model.floor.a * pts[:, 0] + model.floor.b * pts[:, 2] + model.floor.c
    band = pts[(pts[:, 1] - fy > 0.3) & (pts[:, 1] - fy < 1.9)]
    x0, x1 = np.percentile(band[:, 0], [0.5, 99.5]) + np.array([-0.3, 0.3])
    z0, z1 = np.percentile(band[:, 2], [0.5, 99.5]) + np.array([-0.3, 0.3])
    w, h = int((x1 - x0) / res) + 1, int((z1 - z0) / res) + 1
    density = np.zeros((h, w), np.float32)
    ix = ((band[:, 0] - x0) / res).astype(int)
    iz = ((band[:, 2] - z0) / res).astype(int)
    ok = (ix >= 0) & (ix < w) & (iz >= 0) & (iz < h)
    np.add.at(density, (iz[ok], ix[ok]), 1)
    nz = density[density > 0]
    density = np.clip(density / np.percentile(nz, 95), 0, 1) if nz.size else density
    img = cv2.cvtColor((255 - 120 * density).astype(np.uint8), cv2.COLOR_GRAY2BGR)

    def px(p):
        return int((p[0] - x0) / res), int((p[1] - z0) / res)

    for wall in model.walls:
        colour = _PALETTE[wall.id % len(_PALETTE)]
        a, b = px(wall.start_xz), px(wall.end_xz)
        cv2.line(img, a, b, colour, 3)
        mid = ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2)
        nx, nzv = wall.normal_xz
        tip = (int(mid[0] + nx * 12), int(mid[1] + nzv * 12))
        cv2.arrowedLine(img, mid, tip, colour, 1, tipLength=0.4)  # points into the room
        cv2.putText(img, str(wall.id), (tip[0] + 2, tip[1] + 2), cv2.FONT_HERSHEY_SIMPLEX, 0.4, colour, 1)

    lines = [f"walls {len(model.walls)}   manhattan {model.manhattan_deg:.1f} deg"]
    for hgt, sig, area in model.ceiling_heights():
        lines.append(f"ceiling {hgt:.3f} m ({area:.0f} m2)")
    if model.ceiling_note:
        lines.append(model.ceiling_note)
    for k, text in enumerate(lines):
        cv2.putText(img, text, (8, 18 + 16 * k), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), img)
    return out_path
