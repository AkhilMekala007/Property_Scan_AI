"""C7b Room measurement: exact outline, wall lengths, floor area and ceiling height per room.

Uncertainties here are fit-only (plane-fit noise and, for inferred edges, a
fixed wide value). C12 calibration adds depth bias and drift.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

from scan.measure.polygon import (
    INFERRED_SIGMA_M,
    PolygonConfig,
    WallLine,
    arrangement_polygon,
    fallback_lines,
    polygon_area,
)
from scan.rooms import RoomLayout
from scan.semantics.classes import Surface
from scan.structure import StructureModel
from scan.structure.planes import HORIZONTAL_NY, HorizontalPlane, fit_horizontal
from scan.structure.voxels import UNKNOWN

__all__ = ["WallMeasure", "RoomMeasurement", "measure_rooms", "write_measurements", "render_plan"]

AXIS_COS = 0.98  # wall normals within ~11 deg of a grid axis count as axis-aligned
MIN_COVERAGE = 0.3  # an edge backed by less observed wall than this is "inferred"
LOCAL_SHRINK_M = 0.2  # floor / ceiling are re-fitted inside the room, away from its walls


@dataclass
class WallMeasure:
    index: int  # position around the room, corner k -> corner k+1
    length_m: float
    sigma_m: float
    wall_ids: list[int]
    coverage: float
    inferred: bool


@dataclass
class RoomMeasurement:
    id: int
    name: str
    kind: str
    corners_xz: list[tuple[float, float]]
    walls: list[WallMeasure]
    floor_area_m2: float
    floor_area_sigma_m2: float
    perimeter_m: float
    ceiling_height_m: float | None
    ceiling_sigma_m: float | None
    ceiling_note: str | None
    floor_tilt_deg: float | None
    corners_uv: list[tuple[float, float]] = field(default_factory=list, repr=False)

    @property
    def n_inferred(self) -> int:
        return sum(w.inferred for w in self.walls)


def wall_lines(model: StructureModel, layout: RoomLayout) -> tuple[list[WallLine], int]:
    """Fitted walls expressed as axis-aligned lines in the room grid frame; also the count skipped."""
    frame = layout.floor_map.frame
    lines, skipped = [], 0
    for w in model.walls:
        n = frame.xz_to_uv(np.array([w.normal_xz]))[0]
        a, b = frame.xz_to_uv(np.array([w.start_xz, w.end_xz]))
        if abs(n[0]) >= AXIS_COS:
            lines.append(WallLine(w.id, "u", float((a[0] + b[0]) / 2), int(np.sign(n[0])),
                                  (float(min(a[1], b[1])), float(max(a[1], b[1]))), w.sigma_m, w.n_voxels))
        elif abs(n[1]) >= AXIS_COS:
            lines.append(WallLine(w.id, "v", float((a[1] + b[1]) / 2), int(np.sign(n[1])),
                                  (float(min(a[0], b[0])), float(max(a[0], b[0]))), w.sigma_m, w.n_voxels))
        else:
            skipped += 1
    return lines, skipped


def _uv_to_cell(frame, uv: np.ndarray) -> np.ndarray:
    col = np.floor((uv[:, 0] - frame.origin_uv[0]) / frame.cell_m).astype(int)
    row = np.floor((uv[:, 1] - frame.origin_uv[1]) / frame.cell_m).astype(int)
    return np.column_stack([row, col])


MIN_BORDER_M = 0.3  # a wall is a candidate when it borders the room for at least this length
SAMPLE_M = 0.05


def _candidate_lines(layout: RoomLayout, rid: int, lines: list[WallLine]) -> list[WallLine]:
    """Walls that border this room on their inward side for at least MIN_BORDER_M.

    Measured as an absolute length: a long wall plane often runs past several rooms,
    so the stretch along any one room can be a small fraction of it.
    """
    frame, labels = layout.floor_map.frame, layout.labels

    def hits(line, direction):
        along = np.arange(line.span[0], line.span[1] + 1e-9, SAMPLE_M)
        found = np.zeros(len(along), bool)
        for d in (0.1, 0.2, 0.3, 0.5):
            off = line.coord + direction * d
            uv = (np.column_stack([np.full_like(along, off), along]) if line.axis == "u"
                  else np.column_stack([along, np.full_like(along, off)]))
            rc = _uv_to_cell(frame, uv)
            ok = frame.in_bounds(rc)
            hit = np.zeros(len(along), bool)
            hit[ok] = labels[rc[ok, 0], rc[ok, 1]] == rid
            found |= hit
        return found

    out = []
    for line in lines:
        inward = hits(line, line.sign)
        if inward.sum() * SAMPLE_M < MIN_BORDER_M:
            continue
        # The room must lie on the side the wall faces. The C6 region can leak a little past a
        # partition, so a room merely touching the back of another room's wall face is rejected.
        outward = hits(line, -line.sign)
        if outward.sum() > 0.5 * inward.sum():
            continue
        out.append(line)
    return out


def _outline_lines(layout: RoomLayout, rid: int, existing: list[WallLine],
                   min_len: float = 0.3, near: float = 0.04) -> list[WallLine]:
    """Inferred lines along every straight run of the room's region boundary.

    They cut the arrangement where the region really ends, so rectangles are either mostly
    inside or mostly outside and the coverage decision is not borderline. Skipping runs near
    an existing wall (it used to be 25 cm) left big half-covered rectangles that flipped under
    5 mm of pose noise. A real wall a few cm away still wins: its thin strip is covered by
    the region grown towards the wall.
    """
    frame = layout.floor_map.frame
    mask = (layout.labels == rid).astype(np.uint8)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contour = max(contours, key=cv2.contourArea)
    poly = cv2.approxPolyDP(contour, 1.5, True)[:, 0, :].astype(float)
    uv = np.column_stack([frame.origin_uv[0] + (poly[:, 0] + 0.5) * frame.cell_m,
                          frame.origin_uv[1] + (poly[:, 1] + 0.5) * frame.cell_m])
    centre = uv.mean(axis=0)
    out = []
    for k in range(len(uv)):
        a, b = uv[k], uv[(k + 1) % len(uv)]
        du, dv = abs(b[0] - a[0]), abs(b[1] - a[1])
        if max(du, dv) < min_len or min(du, dv) > 0.2 * max(du, dv):
            continue
        axis = "u" if dv > du else "v"
        i = 0 if axis == "u" else 1
        coord = float((a[i] + b[i]) / 2)
        if any(l.axis == axis and l.sign == (1 if centre[i] > coord else -1) and abs(l.coord - coord) <= near
               for l in existing + out):
            continue
        sign = 1 if centre[i] > coord else -1
        span_i = 1 - i
        out.append(WallLine(None, axis, coord, sign,
                            (float(min(a[span_i], b[span_i])), float(max(a[span_i], b[span_i]))),
                            INFERRED_SIGMA_M, 0.0))
    return out


def _region_bounds_uv(layout: RoomLayout, rid: int) -> tuple[float, float, float, float]:
    frame = layout.floor_map.frame
    rows, cols = np.nonzero(layout.labels == rid)
    u0 = frame.origin_uv[0] + cols.min() * frame.cell_m
    u1 = frame.origin_uv[0] + (cols.max() + 1) * frame.cell_m
    v0 = frame.origin_uv[1] + rows.min() * frame.cell_m
    v1 = frame.origin_uv[1] + (rows.max() + 1) * frame.cell_m
    return float(u0), float(u1), float(v0), float(v1)


def _local_planes(model: StructureModel, layout: RoomLayout, corners_uv: np.ndarray, ceiling_level: int | None):
    """Floor and ceiling fitted only from voxels inside this room (shrunk away from its walls)."""
    frame = layout.floor_map.frame
    grid = model.grid
    mask = np.zeros(frame.shape, np.uint8)
    cells = _uv_to_cell(frame, corners_uv)[:, ::-1].astype(np.int32)  # (col, row) for OpenCV
    cv2.fillPoly(mask, [cells], 1)
    r = max(1, int(round(LOCAL_SHRINK_M / frame.cell_m)))
    mask = cv2.erode(mask, np.ones((2 * r + 1, 2 * r + 1), np.uint8)).astype(bool)

    rc = frame.xz_to_cell(grid.centers[:, [0, 2]])
    ok = frame.in_bounds(rc)
    in_room = np.zeros(len(grid), bool)
    in_room[ok] = mask[rc[ok, 0], rc[ok, 1]]
    horizontal = np.abs(grid.normals[:, 1]) > HORIZONTAL_NY
    weights = grid.counts.astype(float)
    p = grid.centers

    gf = model.floor
    above = p[:, 1] - (gf.a * p[:, 0] + gf.b * p[:, 2] + gf.c)
    sel = in_room & horizontal & (np.abs(above) < 0.1) & np.isin(grid.labels, [Surface.FLOOR, UNKNOWN])
    floor = fit_horizontal(p[sel], weights[sel]) or gf

    ceiling: HorizontalPlane | None = None
    if ceiling_level is not None:
        level = model.ceilings[ceiling_level]
        on_level = np.abs(p[:, 1] - (level.a * p[:, 0] + level.b * p[:, 2] + level.c)) < 0.05
        sel = in_room & horizontal & on_level & np.isin(grid.labels, [Surface.CEILING, UNKNOWN])
        ceiling = fit_horizontal(p[sel], weights[sel]) or level
    return floor, ceiling


def measure_rooms(model: StructureModel, layout: RoomLayout,
                  cfg: PolygonConfig | None = None) -> tuple[list[RoomMeasurement], dict]:
    cfg = cfg or PolygonConfig()
    frame = layout.floor_map.frame
    lines, skipped = wall_lines(model, layout)
    results = []
    for room in layout.rooms:
        cands = _candidate_lines(layout, room.id, lines)
        cands += _outline_lines(layout, room.id, cands)
        # outline lines already follow the region boundary; bounding-box fallbacks are only needed
        # when a direction would have fewer than two lines (they created big half-outside rectangles)
        if min(sum(l.axis == a for l in cands) for a in ('u', 'v')) < 2:
            cands += fallback_lines(_region_bounds_uv(layout, room.id), cands, cfg.fallback_gap_m)
        corners_uv, edges = arrangement_polygon(layout.labels == room.id, frame, cands, cfg)
        n = len(edges)
        walls = []
        for k, e in enumerate(edges):
            prev, nxt = edges[k - 1], edges[(k + 1) % n]
            cov = e.coverage
            walls.append(WallMeasure(
                index=k,
                length_m=round(e.length, 4),
                sigma_m=round(float(np.hypot(prev.sigma, nxt.sigma)), 5),
                wall_ids=e.wall_ids,
                coverage=round(cov, 3),
                inferred=(not e.wall_ids) or cov < MIN_COVERAGE,
            ))
        area = polygon_area(corners_uv)
        area_sigma = float(np.sqrt(sum((e.length * e.sigma) ** 2 for e in edges)))

        floor, ceiling = _local_planes(model, layout, corners_uv, room.ceiling_level)
        centroid_xz = frame.uv_to_xz(corners_uv.mean(axis=0, keepdims=True))[0]
        if ceiling is not None:
            h = ceiling.y_at(*centroid_xz) - floor.y_at(*centroid_xz)
            height, h_sigma, note = round(float(h), 4), round(float(np.hypot(ceiling.sigma_m, floor.sigma_m)), 5), None
        else:
            height, h_sigma, note = None, None, room.ceiling_note

        corners_xz = frame.uv_to_xz(corners_uv)
        results.append(RoomMeasurement(
            id=room.id, name=room.name, kind=room.kind,
            corners_xz=[(round(float(x), 4), round(float(z), 4)) for x, z in corners_xz],
            walls=walls,
            floor_area_m2=round(area, 3),
            floor_area_sigma_m2=round(area_sigma, 4),
            perimeter_m=round(float(sum(e.length for e in edges)), 3),
            ceiling_height_m=height, ceiling_sigma_m=h_sigma, ceiling_note=note,
            floor_tilt_deg=round(floor.tilt_deg, 3),
            corners_uv=[(float(u), float(v)) for u, v in corners_uv],
        ))
    diagnostics = {"walls_not_axis_aligned": skipped, "inferred_sigma_m": INFERRED_SIGMA_M}
    return results, diagnostics


def write_measurements(rooms: list[RoomMeasurement], diagnostics: dict, capture_id: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    data = {"capture_id": capture_id, "uncertainty": "fit-only (C12 adds depth bias and drift)",
            "diagnostics": diagnostics, "rooms": []}
    for r in rooms:
        d = asdict(r)
        d.pop("corners_uv")
        d["n_inferred_walls"] = r.n_inferred
        data["rooms"].append(d)
    path = out_dir / "measurements.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def render_plan(rooms: list[RoomMeasurement], layout: RoomLayout, out_path: Path, px_per_m: int = 90,
                openings=None) -> Path:
    """Dimensioned floor plan in the room-aligned frame. Red dashed = inferred wall.

    ``openings`` (from C8) are drawn as gaps: brown = door, blue = window, green = open passage.
    """
    all_uv = np.concatenate([np.array(r.corners_uv) for r in rooms])
    lo, hi = all_uv.min(axis=0) - 0.6, all_uv.max(axis=0) + 0.6
    w, h = (np.ceil((hi - lo) * px_per_m)).astype(int)
    img = np.full((h, w, 3), 255, np.uint8)

    def px(uv):
        return int((uv[0] - lo[0]) * px_per_m), int((uv[1] - lo[1]) * px_per_m)

    fills = [(250, 236, 214), (222, 238, 250), (226, 245, 220), (248, 228, 240), (252, 246, 210),
             (232, 232, 250), (222, 244, 238), (250, 226, 222)]
    for k, r in enumerate(rooms):
        cv2.fillPoly(img, [np.array([px(p) for p in r.corners_uv], np.int32)], fills[k % len(fills)])
    for r in rooms:
        pts = np.array(r.corners_uv)
        centre = pts.mean(axis=0)
        for wm in r.walls:
            a, b = pts[wm.index], pts[(wm.index + 1) % len(pts)]
            if wm.inferred:
                for t in np.arange(0, 1, 0.1):
                    p0, p1 = a + (b - a) * t, a + (b - a) * min(t + 0.05, 1)
                    cv2.line(img, px(p0), px(p1), (40, 40, 220), 2)
            else:
                cv2.line(img, px(a), px(b), (30, 30, 30), 3)
            if wm.length_m >= 0.4:
                mid = (a + b) / 2
                inward = centre - mid
                inward = inward / (np.linalg.norm(inward) + 1e-9)
                label_pt = mid + inward * 0.22
                text = f"{wm.length_m:.2f}"
                (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.38, 1)
                x, y = px(label_pt)
                cv2.putText(img, text, (x - tw // 2, y + th // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.38,
                            (40, 40, 220) if wm.inferred else (60, 60, 60), 1)
        x, y = px(centre)
        lines = [r.name, f"{r.floor_area_m2:.1f} m2",
                 f"h {r.ceiling_height_m:.2f} m" if r.ceiling_height_m is not None else "h n/a"]
        for k, text in enumerate(lines):
            (tw, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
            cv2.putText(img, text, (x - tw // 2, y - 10 + 16 * k), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1)
    if openings:
        by_room = {r.id: r for r in rooms}
        colours = {"door": (40, 90, 160), "window": (220, 140, 30), "opening": (60, 170, 60)}
        for op in openings:
            r = by_room.get(op.room_id)
            if r is None:
                continue
            pts = np.array(r.corners_uv)
            a, b = pts[op.wall_index], pts[(op.wall_index + 1) % len(pts)]
            direction = (b - a) / (np.linalg.norm(b - a) + 1e-12)
            p0 = a + direction * op.offset_m
            p1 = p0 + direction * op.width_m
            cv2.line(img, px(p0), px(p1), (255, 255, 255), 5)
            cv2.line(img, px(p0), px(p1), colours[op.kind], 2)
            if op.kind == "window":
                cv2.line(img, px(p0), px(p1), colours[op.kind], 4)
                cv2.line(img, px(p0), px(p1), (255, 255, 255), 1)
            mid = (p0 + p1) / 2
            cv2.putText(img, f"{op.width_m:.2f}", (px(mid)[0] + 4, px(mid)[1] - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.36, colours[op.kind], 1)
        legend = "lengths in m   red dashed = inferred wall   brown = door   blue = window   green = opening"
    else:
        for d in layout.doorways:
            uv = layout.floor_map.frame.xz_to_uv(np.array([d.center_xz]))[0]
            cv2.circle(img, px(uv), 5, (200, 120, 0), -1)
        legend = "lengths in m   red dashed = inferred wall   orange = doorway"
    cv2.putText(img, legend, (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), img)
    return out_path
