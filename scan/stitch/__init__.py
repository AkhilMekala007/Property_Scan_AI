"""C9 Stitching: separately measured rooms -> one consistent whole-property plan.

LiDAR and video captures already share one world frame, so stitching here is
consistency work: resolve overlaps caused by inferred edges, find shared walls
and their thickness, merge the two views of each opening, build the adjacency
graph and the footprint. Observed walls are never moved.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

from scan.measure import RoomMeasurement, WallMeasure
from scan.measure.polygon import polygon_area
from scan.openings import Opening
from scan.rooms import RoomLayout

__all__ = ["StitchConfig", "SharedWall", "PlanOpening", "PropertyPlan", "stitch", "write_plan"]

RASTER_M = 0.02


@dataclass(frozen=True)
class StitchConfig:
    min_thickness_m: float = 0.03
    max_thickness_m: float = 0.40
    min_shared_m: float = 0.3
    default_thickness_m: float = 0.10
    overlap_tol_m2: float = 0.05
    opening_match_m: float = 0.5
    agree_extra_m: float = 0.02  # two views of one opening agree within 3 sigma + this
    max_edge_move_m: float = 1.0  # larger moves would discard real area: report the overlap instead


@dataclass
class SharedWall:
    room_a: int
    room_b: int
    wall_a: int  # edge index in room_a
    wall_b: int
    thickness_m: float
    length_m: float  # length the two faces run alongside each other


@dataclass
class PlanOpening:
    id: int
    kind: str
    rooms: list[int]  # one room (exterior window / passage) or the two rooms it connects
    walls: list[tuple[int, int]]  # (room id, edge index) for each view
    width_m: float
    width_sigma_m: float
    height_m: float
    head_observed: bool
    sill_m: float
    center_xz: tuple[float, float]
    views: int  # how many rooms measured it
    covered: bool
    low_evidence: bool
    views_agree: bool | None  # None with a single view


@dataclass
class PropertyPlan:
    capture_id: str
    manhattan_deg: float
    rooms: list[RoomMeasurement]
    shared_walls: list[SharedWall]
    openings: list[PlanOpening]
    adjacency: list[tuple[int, int, str]]  # (room a, room b, "door" | "opening" | "open boundary" | "doorway")
    connected_components: int
    unmeasured_doorways: list[int]  # C6 doorways with no measured opening (would be missed openings)
    net_area_m2: float
    footprint_m2: float
    overlaps: list[tuple[int, int, float]]  # remaining room overlaps (room a, room b, m2)
    adjusted_edges: list[tuple[int, int, float]]  # (room, edge, metres moved)
    diagnostics: dict = field(default_factory=dict)


# ---------- rectilinear edge helpers ----------

def _edges(corners: np.ndarray) -> list[tuple[str, float]]:
    out = []
    for k in range(len(corners)):
        a, b = corners[k], corners[(k + 1) % len(corners)]
        out.append(("u", float(a[0])) if abs(a[0] - b[0]) < 1e-9 else ("v", float(a[1])))
    return out


def _corners(edges: list[tuple[str, float]]) -> np.ndarray:
    pts = []
    for k, (axis, coord) in enumerate(edges):
        p_axis, p_coord = edges[k - 1]
        u = p_coord if p_axis == "u" else coord
        v = p_coord if p_axis == "v" else coord
        pts.append((u, v))
    return np.array(pts)


def _inward_sign(corners: np.ndarray, k: int) -> int:
    """+1 when the room lies on the side of increasing coordinate of edge k."""
    a, b = corners[k], corners[(k + 1) % len(corners)]
    mid = (a + b) / 2
    axis = 0 if abs(a[0] - b[0]) < 1e-9 else 1
    probe = mid.copy()
    probe[axis] += 0.01
    inside = cv2.pointPolygonTest(corners.astype(np.float32), (float(probe[0]), float(probe[1])), False) >= 0
    return 1 if inside else -1


def _span(corners: np.ndarray, k: int) -> tuple[float, float]:
    a, b = corners[k], corners[(k + 1) % len(corners)]
    i = 1 if abs(a[0] - b[0]) < 1e-9 else 0
    return float(min(a[i], b[i])), float(max(a[i], b[i]))


# ---------- rasters for overlap and footprint ----------

class _Raster:
    def __init__(self, polys: list[np.ndarray], res: float = RASTER_M, margin: float = 0.5):
        allp = np.concatenate(polys)
        self.lo = allp.min(axis=0) - margin
        hi = allp.max(axis=0) + margin
        self.res = res
        self.shape = (int((hi[1] - self.lo[1]) / res) + 1, int((hi[0] - self.lo[0]) / res) + 1)

    def mask(self, poly: np.ndarray) -> np.ndarray:
        m = np.zeros(self.shape, np.uint8)
        pts = np.round((poly - self.lo) / self.res).astype(np.int32)
        cv2.fillPoly(m, [pts], 1)
        return m.astype(bool)


def _overlaps(rooms: list[RoomMeasurement], tol: float) -> list[tuple[int, int, float]]:
    polys = [np.array(r.corners_uv) for r in rooms]
    raster = _Raster(polys)
    masks = [raster.mask(p) for p in polys]
    cell = raster.res ** 2
    out = []
    for i in range(len(rooms)):
        for j in range(i + 1, len(rooms)):
            area = float((masks[i] & masks[j]).sum() * cell)
            if area > tol:
                out.append((rooms[i].id, rooms[j].id, round(area, 3)))
    return out


# ---------- stitching steps ----------

def find_shared_walls(rooms: list[RoomMeasurement], cfg: StitchConfig) -> list[SharedWall]:
    out = []
    geo = [(r, np.array(r.corners_uv)) for r in rooms]
    for ia, (ra, ca) in enumerate(geo):
        ea = _edges(ca)
        for rb, cb in geo[ia + 1:]:
            eb = _edges(cb)
            for ka, (axa, coa) in enumerate(ea):
                if ra.walls[ka].inferred:
                    continue
                sa = _inward_sign(ca, ka)
                spa = _span(ca, ka)
                for kb, (axb, cob) in enumerate(eb):
                    if axb != axa or rb.walls[kb].inferred:
                        continue
                    sb = _inward_sign(cb, kb)
                    gap = (cob - coa) * -sa  # b's face lies behind a's face
                    if sb != -sa or not (cfg.min_thickness_m <= gap <= cfg.max_thickness_m):
                        continue
                    spb = _span(cb, kb)
                    overlap = min(spa[1], spb[1]) - max(spa[0], spb[0])
                    if overlap >= cfg.min_shared_m:
                        out.append(SharedWall(ra.id, rb.id, ka, kb, round(float(gap), 4), round(float(overlap), 3)))
    return out


def resolve_overlaps(rooms: list[RoomMeasurement], thickness: float, cfg: StitchConfig,
                     frame) -> list[tuple[int, int, float]]:
    """Pull inferred edges that intrude into a neighbour back behind the neighbour's wall."""
    moved: list[tuple[int, int, float]] = []
    by_id = {r.id: r for r in rooms}
    for _ in range(3):
        overlaps = _overlaps(rooms, cfg.overlap_tol_m2)
        if not overlaps:
            break
        changed = False
        for a_id, b_id, _area in overlaps:
            for x_id, o_id in ((a_id, b_id), (b_id, a_id)):
                x, o = by_id[x_id], by_id[o_id]
                cx, co = np.array(x.corners_uv), np.array(o.corners_uv)
                ex, eo = _edges(cx), _edges(co)
                for k, (axis, coord) in enumerate(ex):
                    if not x.walls[k].inferred:
                        continue  # observed walls are never moved
                    a, b = cx[k], cx[(k + 1) % len(cx)]
                    mid = (a + b) / 2
                    if cv2.pointPolygonTest(co.astype(np.float32), (float(mid[0]), float(mid[1])), True) < 0.01:
                        continue  # this edge is not inside the neighbour
                    sx = _inward_sign(cx, k)
                    spx = _span(cx, k)
                    best = None
                    for j, (axo, cco) in enumerate(eo):
                        if axo != axis or _inward_sign(co, j) != -sx:
                            continue
                        spo = _span(co, j)
                        if min(spx[1], spo[1]) - max(spx[0], spo[0]) <= 0:
                            continue
                        t = 0.0 if o.walls[j].inferred else thickness
                        target = cco + sx * t
                        moves_in = (target - coord) * sx > 0 and abs(target - coord) <= cfg.max_edge_move_m
                        if moves_in and (best is None or abs(target - coord) < abs(best - coord)):
                            best = target
                    if best is not None:
                        ex[k] = (axis, float(best))
                        moved.append((x.id, k, round(float(abs(best - coord)), 3)))
                        _apply(x, _corners(ex), frame)
                        changed = True
                        break
        if not changed:
            break
    return moved


def _apply(room: RoomMeasurement, corners: np.ndarray, frame) -> None:
    """Update a room's geometry after edges moved (lengths, area, corners); sigmas are kept."""
    room.corners_uv = [(float(u), float(v)) for u, v in corners]
    room.corners_xz = [(round(float(x), 4), round(float(z), 4)) for x, z in frame.uv_to_xz(corners)]
    n = len(corners)
    room.walls = [WallMeasure(w.index, round(float(np.linalg.norm(corners[(w.index + 1) % n] - corners[w.index])), 4),
                              w.sigma_m, w.wall_ids, w.coverage, w.inferred) for w in room.walls]
    room.floor_area_m2 = round(polygon_area(corners), 3)
    room.perimeter_m = round(sum(w.length_m for w in room.walls), 3)


def merge_openings(openings: list[Opening], cfg: StitchConfig) -> list[PlanOpening]:
    used = set()
    out: list[PlanOpening] = []
    for i, a in enumerate(openings):
        if i in used:
            continue
        group = [a]
        for j in range(i + 1, len(openings)):
            b = openings[j]
            if j in used or b.room_id == a.room_id:
                continue
            dist = np.hypot(a.center_xz[0] - b.center_xz[0], a.center_xz[1] - b.center_xz[1])
            if dist <= cfg.opening_match_m and (a.kind == "window") == (b.kind == "window"):
                group.append(b)
                used.add(j)
        used.add(i)
        out.append(_combine(len(out), group, cfg))
    return out


def _combine(oid: int, views: list[Opening], cfg: StitchConfig) -> PlanOpening:
    good = [v for v in views if not v.covered and not v.low_evidence] or views
    w = np.array([1 / max(v.width_sigma_m, 1e-4) ** 2 for v in good])
    widths = np.array([v.width_m for v in good])
    agree = None
    if len(good) >= 2:
        spread = widths.max() - widths.min()
        agree = bool(spread <= 3 * np.hypot(*[v.width_sigma_m for v in good[:2]]) + cfg.agree_extra_m)
    if agree is False:
        # A doorway is measured on both wall faces; frames and stops make one face's gap
        # wider. The clear opening is the narrowest section through the wall, so take the
        # narrower view and carry the disagreement as uncertainty.
        best = min(good, key=lambda v: v.width_m)
        width, sigma = best.width_m, max(best.width_sigma_m, float(widths.max() - widths.min()) / 2)
    else:
        width = float(np.sum(w * widths) / w.sum())
        sigma = float(1 / np.sqrt(w.sum()))
    kinds = [v.kind for v in views]
    kind = "door" if "door" in kinds else kinds[0]
    rooms = sorted({v.room_id for v in views} | {v.connects_room for v in views if v.connects_room})
    head_seen = [v for v in views if v.head_observed]
    head = float(np.median([v.height_m for v in head_seen])) if head_seen else max(v.height_m for v in views)
    centre = np.mean([v.center_xz for v in views], axis=0)
    return PlanOpening(
        id=oid, kind=kind, rooms=rooms, walls=[(v.room_id, v.wall_index) for v in views],
        width_m=round(width, 4), width_sigma_m=round(sigma, 4), height_m=round(head, 3),
        head_observed=bool(head_seen), sill_m=round(float(np.median([v.sill_m for v in views])), 3),
        center_xz=(round(float(centre[0]), 4), round(float(centre[1]), 4)), views=len(views),
        covered=all(v.covered for v in views), low_evidence=all(v.low_evidence for v in views),
        views_agree=agree,
    )


def _components(n_rooms: list[int], edges: list[tuple[int, int, str]]) -> int:
    parent = {r: r for r in n_rooms}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, _ in edges:
        if a in parent and b in parent:
            parent[find(a)] = find(b)
    return len({find(r) for r in n_rooms})


def stitch(rooms: list[RoomMeasurement], openings: list[Opening], layout: RoomLayout,
           cfg: StitchConfig | None = None) -> PropertyPlan:
    cfg = cfg or StitchConfig()
    frame = layout.floor_map.frame

    shared = find_shared_walls(rooms, cfg)
    thickness = float(np.median([s.thickness_m for s in shared])) if shared else cfg.default_thickness_m
    moved = resolve_overlaps(rooms, thickness, cfg, frame)
    shared = find_shared_walls(rooms, cfg)
    overlaps = _overlaps(rooms, cfg.overlap_tol_m2)

    merged = merge_openings(openings, cfg)
    adjacency: list[tuple[int, int, str]] = []
    for op in merged:
        if len(op.rooms) == 2:
            adjacency.append((op.rooms[0], op.rooms[1], op.kind))
    linked = {tuple(sorted((a, b))) for a, b, _ in adjacency}
    unmeasured = []
    for d in layout.doorways:
        pair = tuple(sorted((d.room_a, d.room_b)))
        if pair not in linked:
            near = any(np.hypot(o.center_xz[0] - d.center_xz[0], o.center_xz[1] - d.center_xz[1]) <= 0.7
                       for o in merged)
            adjacency.append((pair[0], pair[1], "doorway" if not near else "door"))
            linked.add(pair)
            if not near:
                unmeasured.append(d.id)

    polys = [np.array(r.corners_uv) for r in rooms]
    raster = _Raster(polys)
    union = np.zeros(raster.shape, np.uint8)
    for p in polys:
        union |= raster.mask(p)
    k = int(np.ceil(cfg.max_thickness_m / raster.res))
    closed = cv2.morphologyEx(union, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))  # bridge wall gaps
    footprint = float(closed.sum() * raster.res ** 2)

    return PropertyPlan(
        capture_id=layout.capture_id,
        manhattan_deg=layout.manhattan_deg,
        rooms=rooms,
        shared_walls=shared,
        openings=merged,
        adjacency=adjacency,
        connected_components=_components([r.id for r in rooms], adjacency),
        unmeasured_doorways=unmeasured,
        net_area_m2=round(sum(r.floor_area_m2 for r in rooms), 3),
        footprint_m2=round(footprint, 3),
        overlaps=overlaps,
        adjusted_edges=moved,
        diagnostics={"median_wall_thickness_m": round(thickness, 4), "n_shared_walls": len(shared)},
    )


def write_plan(plan: PropertyPlan, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    d = asdict(plan)
    for r in d["rooms"]:
        r.pop("corners_uv", None)
    path = out_dir / "plan.json"
    path.write_text(json.dumps(d, indent=2), encoding="utf-8")
    return path
