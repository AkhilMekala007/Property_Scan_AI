"""Render the stitched whole-property floor plan (the product surface)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from scan.stitch import PropertyPlan, _edges, _inward_sign

PX_PER_M = 100
FILLS = [(244, 236, 222), (226, 236, 246), (228, 242, 226), (244, 230, 238), (248, 244, 222),
         (234, 232, 248), (224, 242, 238), (246, 228, 224)]
WALL = (55, 55, 60)
DOOR = (40, 90, 160)
WINDOW = (200, 130, 40)
TEXT = (30, 30, 30)
DIM = (90, 90, 90)


def render_floor_plan(plan: PropertyPlan, frame, out_path: Path, title: str | None = None,
                      damage=None) -> Path:
    t = plan.diagnostics.get("median_wall_thickness_m", 0.1)
    polys = [np.array(r.corners_uv) for r in plan.rooms]
    allp = np.concatenate(polys)
    lo = allp.min(axis=0) - 1.0
    hi = allp.max(axis=0) + np.array([1.0, 1.6])
    w, h = (np.ceil((hi - lo) * PX_PER_M)).astype(int)
    img = np.full((h, w, 3), 255, np.uint8)

    def px(p):
        return int(round((p[0] - lo[0]) * PX_PER_M)), int(round((p[1] - lo[1]) * PX_PER_M))

    # floors
    for k, poly in enumerate(polys):
        cv2.fillPoly(img, [np.array([px(p) for p in poly], np.int32)], FILLS[k % len(FILLS)])

    # walls: each observed edge drawn outward at the measured thickness; inferred edges stay open
    wall_px = max(3, int(round(t * PX_PER_M)))
    for room, poly in zip(plan.rooms, polys):
        for k, (axis, _) in enumerate(_edges(poly)):
            a, b = poly[k], poly[(k + 1) % len(poly)]
            d = (b - a) / (np.linalg.norm(b - a) + 1e-12)
            normal_in = np.zeros(2)
            normal_in[0 if axis == "u" else 1] = _inward_sign(poly, k)
            off = -normal_in * t / 2
            if room.walls[k].inferred:
                for s in np.arange(0, 1, 0.08):
                    p0, p1 = a + (b - a) * s, a + (b - a) * min(s + 0.04, 1)
                    cv2.line(img, px(p0), px(p1), (170, 170, 170), 1)
            else:
                cv2.line(img, px(a + off - d * t / 2), px(b + off + d * t / 2), WALL, wall_px)

    # openings: clear the wall, then draw the symbol
    by_id = {r.id: (r, p) for r, p in zip(plan.rooms, polys)}
    for op in plan.openings:
        room_id, k = op.walls[0]
        room, poly = by_id[room_id]
        a, b = poly[k], poly[(k + 1) % len(poly)]
        axis = "u" if abs(a[0] - b[0]) < 1e-9 else "v"
        d = (b - a) / (np.linalg.norm(b - a) + 1e-12)
        n_in = np.zeros(2)
        n_in[0 if axis == "u" else 1] = _inward_sign(poly, k)
        c = frame.xz_to_uv(np.array([op.center_xz]))[0]
        c[0 if axis == "u" else 1] = a[0 if axis == "u" else 1]  # onto the wall face
        p0, p1 = c - d * op.width_m / 2, c + d * op.width_m / 2
        span = max(t, 0.08) * 1.2
        quad = np.array([px(p0 + n_in * 0.03), px(p1 + n_in * 0.03),
                         px(p1 - n_in * span), px(p0 - n_in * span)], np.int32)
        cv2.fillPoly(img, [quad], (255, 255, 255))
        if op.kind == "door":
            r = int(op.width_m * PX_PER_M)
            hinge = px(p0)
            leaf_end = px(p0 + n_in * op.width_m)
            cv2.line(img, hinge, leaf_end, DOOR, 2)
            start = np.degrees(np.arctan2(n_in[1], n_in[0]))
            end = np.degrees(np.arctan2(d[1], d[0]))
            if (end - start) % 360 > 180:
                start, end = end, start
            cv2.ellipse(img, hinge, (r, r), 0, start, start + ((end - start) % 360), DOOR, 1)
        elif op.kind == "window":
            for off in (0.0, -t / 2, -t):
                cv2.line(img, px(p0 + n_in * off), px(p1 + n_in * off), WINDOW, 2 if off == -t / 2 else 1)
        mid = c + n_in * 0.18
        label = f"{op.width_m:.2f}"
        cv2.putText(img, label, (px(mid)[0] - 12, px(mid)[1] + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.38,
                    DOOR if op.kind == "door" else WINDOW if op.kind == "window" else DIM, 1, cv2.LINE_AA)

    # dimensions and room labels
    for room, poly in zip(plan.rooms, polys):
        centre = poly.mean(axis=0)
        for k, wm in enumerate(room.walls):
            if wm.inferred or wm.length_m < 0.6:
                continue
            a, b = poly[k], poly[(k + 1) % len(poly)]
            mid = (a + b) / 2
            inward = centre - mid
            inward /= np.linalg.norm(inward) + 1e-9
            text = f"{wm.length_m:.2f}"
            (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.36, 1)
            x, y = px(mid + inward * 0.25)
            cv2.putText(img, text, (x - tw // 2, y + th // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.36, DIM, 1, cv2.LINE_AA)
        lines = [room.name.replace("_", " ").title(), f"{room.floor_area_m2:.1f} m2"]
        if room.ceiling_height_m is not None:
            lines.append(f"ceiling {room.ceiling_height_m:.2f} m")
        x, y = px(centre)
        for i, text in enumerate(lines):
            scale = 0.55 if i == 0 else 0.42
            (tw, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
            cv2.putText(img, text, (x - tw // 2, y - 8 + 18 * i), cv2.FONT_HERSHEY_SIMPLEX, scale, TEXT,
                        2 if i == 0 else 1, cv2.LINE_AA)

    # damage: floor / ceiling regions as outlines in the plan, wall damage as a marker on its wall
    if damage:
        colours = {"water_stain": (30, 110, 200), "crack": (40, 40, 200), "mold": (60, 120, 40)}
        for d in damage:
            room, poly = by_id.get(d.room_id, (None, None))
            if room is None:
                continue
            colour = colours.get(d.cls, (0, 0, 200))
            label = f"{d.cls.replace('_', ' ')} {d.area_m2:.2f} m2" + (" (ceiling)" if d.surface_kind == "ceiling" else "")
            if d.surface_kind in ("floor", "ceiling") and d.outline_2d:
                pts = np.array([px(p) for p in d.outline_2d], np.int32)
                if d.surface_kind == "ceiling":
                    for k in range(len(pts)):
                        if k % 2 == 0:
                            cv2.line(img, tuple(pts[k]), tuple(pts[(k + 1) % len(pts)]), colour, 2)
                else:
                    cv2.polylines(img, [pts], True, colour, 2)
                anchor = pts.mean(axis=0).astype(int)
            else:
                k = d.wall_index
                a, b = poly[k], poly[(k + 1) % len(poly)]
                axis = 0 if abs(a[0] - b[0]) < 1e-9 else 1
                pos = np.zeros(2)
                pos[axis] = a[axis]
                pos[1 - axis] = d.centroid_2d[0]
                inward = np.zeros(2)
                inward[axis] = _inward_sign(poly, k)
                tip = px(pos + inward * 0.12)
                tri = np.array([tip, px(pos + inward * 0.32 + (b - a) / (np.linalg.norm(b - a) + 1e-9) * 0.1),
                                px(pos + inward * 0.32 - (b - a) / (np.linalg.norm(b - a) + 1e-9) * 0.1)], np.int32)
                cv2.fillPoly(img, [tri], colour)
                anchor = np.array(px(pos + inward * 0.45))
            cv2.putText(img, label, (int(anchor[0]) - 40, int(anchor[1]) + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                        colour, 1, cv2.LINE_AA)

    # header, scale bar, legend
    title = title or f"{plan.capture_id}  -  {len(plan.rooms)} rooms, {plan.net_area_m2:.1f} m2 net"
    cv2.putText(img, title, (16, h - 52), cv2.FONT_HERSHEY_SIMPLEX, 0.55, TEXT, 1, cv2.LINE_AA)
    x0, y0 = 16, h - 24
    cv2.line(img, (x0, y0), (x0 + PX_PER_M, y0), TEXT, 2)
    for xx in (x0, x0 + PX_PER_M):
        cv2.line(img, (xx, y0 - 5), (xx, y0 + 5), TEXT, 2)
    cv2.putText(img, "1 m", (x0 + PX_PER_M + 8, y0 + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.45, TEXT, 1, cv2.LINE_AA)
    cv2.putText(img, "dimensions in metres   grey dashed = open / unobserved side", (x0 + 170, y0 + 5),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, DIM, 1, cv2.LINE_AA)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), img)
    return out_path
