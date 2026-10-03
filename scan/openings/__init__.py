"""C8 Openings: doors, windows and open passages in each room's walls, with widths and heights.

Widths here come from depth only (jamb edges from fused voxels). RGB edge
refinement is the planned fix-loop step for the opening-width gate.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

from scan.core.types import FrameSet
from scan.measure import RoomMeasurement
from scan.openings.elevation import Elevation, ElevationConfig, WallSpec, build_elevation, render_elevation
from scan.rooms import RoomLayout
from scan.structure import StructureModel

__all__ = ["Opening", "OpeningConfig", "find_openings", "write_openings"]

DEFAULT_HEIGHT_M = 2.7
VOXEL_HALF_M = 0.01  # voxel centroids sit ~half a voxel inside the true surface edge
INFERRED_SIGMA_M = 0.05
EDGE_K = 3  # per-row edge = 3rd outermost ray crossing


@dataclass(frozen=True)
class OpeningConfig:
    elevation: ElevationConfig = field(default_factory=ElevationConfig)
    min_edge_m: float = 0.5
    min_through: int = 2  # rays per cell for seen-through evidence
    min_label: int = 1
    close_m: float = 0.06
    min_width_m: float = 0.3
    min_height_m: float = 0.3
    floor_tol_m: float = 0.15  # a door reaches down to the floor
    door_min_height_m: float = 1.6
    window_min_sill_m: float = 0.25
    jamb_search_m: float = 0.3
    doorway_link_m: float = 0.7
    min_open_rays: int = 200  # an open door / passage must be seen through by this many rays
    closed_door_width_m: tuple[float, float] = (0.6, 1.3)  # label-only doors (closed) must look door-sized
    closed_door_min_top_m: float = 1.8
    merge_gap_m: float = 0.3  # stacked pieces closer than this (frame bars, transoms) are one opening
    max_door_width_m: float = 1.5  # wider see-through gaps reaching the floor are open passages
    jamb_agree_m: float = 0.04  # a wall jamb this close to the ray edge refines it
    low_evidence_rays: int = 5000  # fewer rays: edges fall short of the jambs, so widen sigma


@dataclass
class Opening:
    id: int
    room_id: int
    room_name: str
    wall_index: int  # edge index in the room's measurement
    wall_ids: list[int]
    kind: str  # door | window | opening
    offset_m: float  # from the wall's start corner to the opening's near jamb
    width_m: float
    width_sigma_m: float
    height_m: float  # top of the opening above the floor
    sill_m: float  # bottom above the floor (0 for doors)
    center_xz: tuple[float, float]
    jambs_observed: int  # 0-2 jambs backed by wall evidence
    covered: bool  # seen only via labels (closed door, curtain): extent less certain
    head_observed: bool  # False when the wall above was never seen (top is a lower bound)
    low_evidence: bool  # few rays through an open gap: width likely underestimated
    evidence: dict
    connects_room: int | None = None


def _edge_specs(room: RoomMeasurement) -> list[tuple[int, WallSpec, float]]:
    pts = np.array(room.corners_uv)
    centre = pts.mean(axis=0)
    height = room.ceiling_height_m or DEFAULT_HEIGHT_M
    out = []
    for k in range(len(pts)):
        a, b = pts[k], pts[(k + 1) % len(pts)]
        if abs(a[0] - b[0]) < 1e-9:
            axis, coord, along = "u", a[0], (a[1], b[1])
        else:
            axis, coord, along = "v", a[1], (a[0], b[0])
        i = 0 if axis == "u" else 1
        sign = 1 if centre[i] > coord else -1
        out.append((k, WallSpec(axis, float(coord), sign, float(min(along)), float(max(along)), height),
                    float(along[0])))
    return out


def _jamb(el: Elevation, edge_s: float, side: int, h_lo: float, h_hi: float, search: float):
    """Sub-cell jamb position: per height row, the solid point closest to the opening; median over rows.

    side = -1 for the left jamb (solid to the left), +1 for the right jamb.
    """
    s, h = el.solid_s, el.solid_h
    if side < 0:
        sel = (s > edge_s - search) & (s < edge_s + 0.04)
    else:
        sel = (s < edge_s + search) & (s > edge_s - 0.04)
    sel &= (h > h_lo) & (h < h_hi)
    if sel.sum() < 5:
        return None
    rows = np.floor(h[sel] / el.res).astype(int)
    ss = s[sel]
    per_row = []
    for r in np.unique(rows):
        v = ss[rows == r]
        per_row.append(v.max() + VOXEL_HALF_M if side < 0 else v.min() - VOXEL_HALF_M)
    per_row = np.array(per_row)
    if len(per_row) < 3:
        return None
    med = float(np.median(per_row))
    mad = float(np.median(np.abs(per_row - med))) * 1.4826
    return med, max(mad, 0.003) / np.sqrt(len(per_row)), len(per_row)


def _ray_edges(el: Elevation, s_lo: float, s_hi: float, h_lo: float, h_hi: float):
    """Clear-opening edges from exact ray crossings: per height row, the EDGE_K-th outermost
    crossing on each side; median over rows. Returns (left, right, sigma_l, sigma_r) or None."""
    s, h = el.cross_s, el.cross_h
    sel = (s > s_lo - 0.05) & (s < s_hi + 0.05) & (h > h_lo) & (h < h_hi)
    if sel.sum() < 50:
        return None
    rows = np.floor(h[sel] / el.res).astype(int)
    ss = s[sel]
    lefts, rights = [], []
    for r in np.unique(rows):
        v = np.sort(ss[rows == r])
        if len(v) >= 10:
            # k-th outermost crossing: robust to a stray ray, and unlike a percentile its
            # inward bias shrinks with ray density instead of growing with the opening's width
            lefts.append(v[EDGE_K - 1])
            rights.append(v[-EDGE_K])
    if len(lefts) < 5:
        return None
    lefts, rights = np.array(lefts), np.array(rights)

    def robust(x):
        med = float(np.median(x))
        return med, max(float(np.median(np.abs(x - med))) * 1.4826, 0.003) / np.sqrt(len(x))

    (l, sl), (r, sr) = robust(lefts), robust(rights)
    return l, r, sl, sr


def _head(el: Elevation, s_lo: float, s_hi: float, top: float):
    """Height of the solid edge above an opening (door head / window top)."""
    s, h = el.solid_s, el.solid_h
    sel = (s > s_lo + 0.05) & (s < s_hi - 0.05) & (h > top - 0.04) & (h < top + 0.4)
    if sel.sum() < 5:
        return None
    return float(np.percentile(h[sel], 5)) - VOXEL_HALF_M


def _sill(el: Elevation, s_lo: float, s_hi: float, bottom: float):
    s, h = el.solid_s, el.solid_h
    sel = (s > s_lo + 0.05) & (s < s_hi - 0.05) & (h < bottom + 0.04) & (h > bottom - 0.4)
    if sel.sum() < 5:
        return None
    return float(np.percentile(h[sel], 95)) + VOXEL_HALF_M


def _components(el: Elevation, cfg: OpeningConfig) -> list[tuple[int, int, int, int, np.ndarray]]:
    evidence = (el.through >= cfg.min_through) | (el.door >= cfg.min_label) | (el.window >= cfg.min_label)
    open_mask = (evidence & ~el.solid).astype(np.uint8)
    k = max(1, int(round(cfg.close_m / el.res)))
    open_mask = cv2.morphologyEx(open_mask, cv2.MORPH_CLOSE, np.ones((2 * k + 1, 2 * k + 1), np.uint8))
    open_mask &= (~el.solid).astype(np.uint8)
    n, comp, stats, _ = cv2.connectedComponentsWithStats(open_mask, connectivity=8)
    boxes = [[int(stats[i, 0]), int(stats[i, 1]), int(stats[i, 0] + stats[i, 2]), int(stats[i, 1] + stats[i, 3]),
              comp == i] for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= 4]
    gap = int(round(cfg.merge_gap_m / el.res))
    merged = True
    while merged:  # one window split by frame bars / transoms becomes one opening
        merged = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                overlap = min(a[2], b[2]) - max(a[0], b[0])
                narrower = min(a[2] - a[0], b[2] - b[0])
                v_gap = max(a[1], b[1]) - min(a[3], b[3])
                if narrower > 0 and overlap >= 0.5 * narrower and v_gap <= gap:
                    boxes[i] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]), a[4] | b[4]]
                    boxes.pop(j)
                    merged = True
                    break
            if merged:
                break
    return [tuple(b) for b in boxes
            if (b[2] - b[0]) * el.res >= cfg.min_width_m and (b[3] - b[1]) * el.res >= cfg.min_height_m]


def find_openings(model: StructureModel, layout: RoomLayout, rooms: list[RoomMeasurement],
                  frameset: FrameSet, cfg: OpeningConfig | None = None,
                  debug_dir: Path | None = None) -> list[Opening]:
    cfg = cfg or OpeningConfig()
    frame2d = layout.floor_map.frame
    posed = [f for f in frameset.frames if f.T_world_cam is not None and f.has_depth]
    cam_cells = frame2d.xz_to_cell(np.array([f.T_world_cam[[0, 2], 3] for f in posed]))
    ok = frame2d.in_bounds(cam_cells)
    cam_room = np.zeros(len(posed), int)
    cam_room[ok] = layout.labels[cam_cells[ok, 0], cam_cells[ok, 1]]

    openings: list[Opening] = []
    for room in rooms:
        in_room = [f for f, r in zip(posed, cam_room) if r == room.id]
        step = max(1, len(in_room) // cfg.elevation.max_frames)
        frames = in_room[::step]
        for k, spec, start in _edge_specs(room):
            if spec.s1 - spec.s0 < cfg.min_edge_m or not room.walls[k].wall_ids:
                continue  # an edge with no fitted wall is an open boundary, not a wall with openings
                # (low coverage alone is kept: a window wall is mostly glass, so it is rarely 'seen')
            el = build_elevation(spec, model, frame2d, frames, cfg.elevation)
            boxes = []
            for c0, r0, c1, r1, mask in _components(el, cfg):
                op = _classify_and_measure(el, c0, r0, c1, r1, mask, cfg)
                if op is None:
                    continue
                kind, left, right, w_sigma, n_jambs, head, sill, covered, head_seen, evidence = op
                mid_s = (left + right) / 2
                i = 0 if spec.axis == "u" else 1
                uv = np.zeros(2)
                uv[i], uv[1 - i] = spec.coord, mid_s
                centre = frame2d.uv_to_xz(uv[None])[0]
                offset = min(abs(left - start), abs(right - start))
                openings.append(Opening(
                    id=len(openings), room_id=room.id, room_name=room.name, wall_index=k,
                    wall_ids=room.walls[k].wall_ids, kind=kind,
                    offset_m=round(offset, 3), width_m=round(right - left, 4), width_sigma_m=round(w_sigma, 4),
                    height_m=round(head, 3), sill_m=round(sill, 3),
                    center_xz=(round(float(centre[0]), 4), round(float(centre[1]), 4)),
                    jambs_observed=n_jambs, covered=covered, head_observed=head_seen,
                    low_evidence=evidence["low_evidence"], evidence=evidence,
                ))
                colour = {"door": (40, 40, 200), "window": (200, 60, 160), "opening": (40, 160, 40)}[kind]
                boxes.append(((left - spec.s0) / el.res, sill / el.res, (right - spec.s0) / el.res,
                              head / el.res, colour))
            if debug_dir is not None:
                debug_dir.mkdir(parents=True, exist_ok=True)
                render_elevation(el, debug_dir / f"{room.name}_wall{k:02d}.png", boxes)

    for op in openings:  # which room does each door lead to?
        best = None
        for d in layout.doorways:
            dist = float(np.hypot(op.center_xz[0] - d.center_xz[0], op.center_xz[1] - d.center_xz[1]))
            if dist <= cfg.doorway_link_m and op.room_id in (d.room_a, d.room_b) and (best is None or dist < best[0]):
                best = (dist, d.room_b if op.room_id == d.room_a else d.room_a)
        if best is not None:
            op.connects_room = best[1]
            if op.kind == "opening":
                op.kind = "door" if any(dw.kind == "door" for dw in layout.doorways
                                        if op.room_id in (dw.room_a, dw.room_b)
                                        and best[1] in (dw.room_a, dw.room_b)) else "opening"
    return openings


def _classify_and_measure(el: Elevation, c0, r0, c1, r1, mask, cfg: OpeningConfig):
    bottom, top = r0 * el.res, r1 * el.res
    s_left, s_right = el.s_of_col(c0), el.s_of_col(c1)
    n_through = int(el.through[mask].sum())
    n_door = int(el.door[mask].sum())
    n_window = int(el.window[mask].sum())
    reaches_floor = bottom <= cfg.floor_tol_m
    # top of the wall that was actually observed: a gap running up to it may simply have an unseen head
    wall_top = float(np.percentile(el.solid_h, 99)) if len(el.solid_h) else 0.0
    runs_to_unseen = top >= wall_top - 0.1 and (top - bottom) >= 1.0
    tall = (top - bottom) >= cfg.door_min_height_m or runs_to_unseen
    seen_through = n_through >= cfg.min_open_rays
    if reaches_floor and tall:
        if seen_through:
            kind = "door" if n_door > 10 else "opening"
        elif n_door > 10:
            kind = "door"  # closed door: checked for door-like size below
        elif n_window > 10:
            kind = "window"  # floor-length curtain or covered balcony door
        else:
            return None
        h_lo, h_hi = 0.3, min(top - 0.15, 1.9)
    elif bottom >= cfg.window_min_sill_m:
        kind = "window"
        h_lo, h_hi = bottom + 0.05, top - 0.05
        if n_through == 0 and n_window == 0:
            return None
    else:
        return None  # low gap that doesn't reach the floor: furniture shadow, not an opening
    covered = not seen_through

    jl = _jamb(el, s_left, -1, h_lo, h_hi, cfg.jamb_search_m)
    jr = _jamb(el, s_right, +1, h_lo, h_hi, cfg.jamb_search_m)
    rays = _ray_edges(el, s_left, s_right, h_lo, h_hi) if seen_through else None
    if rays is not None:
        # the clear opening is where rays passed through; a wall jamb right at that edge refines it
        left, right, sig_l, sig_r = rays
        if jl and abs(jl[0] - left) <= cfg.jamb_agree_m:
            left, sig_l = jl[0], jl[1]
        if jr and abs(jr[0] - right) <= cfg.jamb_agree_m:
            right, sig_r = jr[0], jr[1]
    else:
        left = jl[0] if jl else s_left
        right = jr[0] if jr else s_right
        sig_l = jl[1] if jl else INFERRED_SIGMA_M
        sig_r = jr[1] if jr else INFERRED_SIGMA_M
    if right - left < cfg.min_width_m:
        return None
    head_found = _head(el, left, right, top)
    head = head_found or top
    if kind == "door" and not covered and right - left > cfg.max_door_width_m:
        kind = "opening"  # too wide for a door: an open passage between spaces
    if kind == "door" and covered:
        lo, hi = cfg.closed_door_width_m
        if not (lo <= right - left <= hi and (head >= cfg.closed_door_min_top_m or head_found is None)):
            return None  # door-labelled but not door-shaped: wardrobe or cabinet doors
    sill = 0.0 if kind != "window" else (_sill(el, left, right, bottom) or bottom)
    if covered and kind == "window":  # curtain: the window's real extent is hidden
        sig_l = sig_r = INFERRED_SIGMA_M
    evidence = {"through_rays": n_through, "door_votes": n_door, "window_votes": n_window}
    n_jambs = int(jl is not None) + int(jr is not None)
    head_seen = head_found is not None
    sigma = float(np.hypot(sig_l, sig_r))
    low_evidence = (not covered) and n_through < cfg.low_evidence_rays
    if low_evidence:
        sigma = max(sigma, INFERRED_SIGMA_M)
    evidence["low_evidence"] = low_evidence
    return kind, left, right, sigma, n_jambs, head, sill, covered, head_seen, evidence


def write_openings(openings: list[Opening], capture_id: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "openings.json"
    data = {"capture_id": capture_id,
            "uncertainty": "fit-only, depth-based jambs (RGB edge refinement not applied)",
            "openings": [asdict(o) for o in openings]}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path
