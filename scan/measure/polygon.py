"""Room polygon from wall lines (a line arrangement), not from the ragged floor-map outline.

In the room-aligned (u, v) frame every fitted wall is a line ``u = const`` or
``v = const``. The lines facing into a room cut the plane into rectangles; a
rectangle belongs to the room when most of it is covered by the room's
floor-map region. The outline of the chosen rectangles is the room polygon, so
every edge lies exactly on a fitted wall. A side with no observed wall gets a
fallback line from the region's extent, flagged as inferred.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

INFERRED_SIGMA_M = 0.05


@dataclass
class WallLine:
    """A fitted wall plane in the (u, v) frame, or an inferred fallback line (wall_id None)."""

    wall_id: int | None
    axis: str  # "u": the line u = coord ; "v": the line v = coord
    coord: float
    sign: int  # +1 when the wall faces increasing coord (room on the + side)
    span: tuple[float, float]  # observed extent along the line
    sigma: float
    weight: float

    @property
    def inferred(self) -> bool:
        return self.wall_id is None


@dataclass
class Edge:
    axis: str
    coord: float
    span: tuple[float, float]
    lines: list[WallLine] = field(default_factory=list)

    @property
    def length(self) -> float:
        return self.span[1] - self.span[0]

    @property
    def wall_ids(self) -> list[int]:
        return sorted(l.wall_id for l in self.lines if l.wall_id is not None)

    @property
    def sigma(self) -> float:
        real = [l for l in self.lines if not l.inferred]
        if not real:
            return INFERRED_SIGMA_M
        w = np.array([l.weight for l in real])
        s = np.array([l.sigma for l in real])
        return float(np.sqrt(np.sum((w / w.sum()) ** 2 * s ** 2)))

    @property
    def coverage(self) -> float:
        """Share of the edge backed by observed wall."""
        spans = sorted((max(l.span[0], self.span[0]), min(l.span[1], self.span[1]))
                       for l in self.lines if not l.inferred)
        covered, end = 0.0, -np.inf
        for a, b in spans:
            a = max(a, end)
            if b > a:
                covered += b - a
                end = b
        return float(min(1.0, covered / self.length)) if self.length > 0 else 0.0


@dataclass
class PolygonConfig:
    merge_tol_m: float = 0.03  # walls this close and parallel are the same line
    strong_threshold: float = 0.6  # a rectangle this covered is certainly in the room
    weak_threshold: float = 0.4  # ...this covered, only if connected to a certain one
    bridge_threshold: float = 0.1  # a sandwiched strip run needs at least this coverage somewhere
    max_strip_m: float = 0.35  # thin strips stacked up to this size between room pieces are inside
    bridge_m: float = 0.10  # room region is grown by this much to reach the wall faces
    fallback_gap_m: float = 0.4  # no wall within this of the region's extent -> inferred line


def merge_lines(lines: list[WallLine], tol: float) -> list[list[WallLine]]:
    """Group parallel, same-facing lines closer than ``tol``."""
    groups: list[list[WallLine]] = []
    for line in sorted(lines, key=lambda l: (l.axis, l.sign, l.coord)):
        g = groups[-1] if groups else None
        if g and g[0].axis == line.axis and g[0].sign == line.sign and abs(line.coord - g[-1].coord) <= tol:
            g.append(line)
        else:
            groups.append([line])
    return groups


def group_coord(group: list[WallLine]) -> float:
    real = [l for l in group if not l.inferred] or group
    return float(np.average([l.coord for l in real], weights=[max(l.weight, 1e-6) for l in real]))


def fallback_lines(region_uv_bounds: tuple[float, float, float, float], lines: list[WallLine],
                   gap: float) -> list[WallLine]:
    """Inferred lines on sides of the region that no observed wall bounds."""
    u0, u1, v0, v1 = region_uv_bounds
    out = []
    for axis, coord, sign, span in (("u", u0, +1, (v0, v1)), ("u", u1, -1, (v0, v1)),
                                    ("v", v0, +1, (u0, u1)), ("v", v1, -1, (u0, u1))):
        bounded = any(l.axis == axis and l.sign == sign and abs(l.coord - coord) <= gap for l in lines)
        if not bounded:
            out.append(WallLine(None, axis, coord, sign, span, INFERRED_SIGMA_M, 0.0))
    return out


def arrangement_polygon(
    region: np.ndarray,
    frame,
    lines: list[WallLine],
    cfg: PolygonConfig | None = None,
) -> tuple[np.ndarray, list[Edge]]:
    """Room region (bool grid) + candidate lines -> (corners (N, 2) uv, edges).

    ``frame`` is the room grid frame (origin_uv, cell_m) the region is drawn in.
    Edge k runs from corner k to corner k+1.
    """
    cfg = cfg or PolygonConfig()
    groups = merge_lines(lines, cfg.merge_tol_m)
    u_groups = sorted((g for g in groups if g[0].axis == "u"), key=group_coord)
    v_groups = sorted((g for g in groups if g[0].axis == "v"), key=group_coord)
    if len(u_groups) < 2 or len(v_groups) < 2:
        raise ValueError("need at least two lines in each direction to close a room")
    U = np.array([group_coord(g) for g in u_groups])
    V = np.array([group_coord(g) for g in v_groups])

    # coverage of each rectangle by the (slightly grown) room region, via an integral image
    cell_m = frame.cell_m
    r = max(1, int(round(cfg.bridge_m / cell_m)))
    grown = cv2.dilate(region.astype(np.uint8), np.ones((2 * r + 1, 2 * r + 1), np.uint8))
    integral = cv2.integral(grown)  # (h+1, w+1)
    h, w = region.shape

    def col_of(u):
        return int(np.clip(np.round((u - frame.origin_uv[0]) / cell_m), 0, w))

    def row_of(v):
        return int(np.clip(np.round((v - frame.origin_uv[1]) / cell_m), 0, h))

    cov = np.zeros((len(V) - 1, len(U) - 1))
    for i in range(len(V) - 1):
        r0, r1 = row_of(V[i]), row_of(V[i + 1])
        r1 = min(max(r1, r0 + 1), h)  # slivers thinner than a grid cell are measured over one cell;
        r0 = r1 - 1 if r1 - r0 < 1 else r0  # with zero area their coverage read 0 and split rooms
        for j in range(len(U) - 1):
            c0, c1 = col_of(U[j]), col_of(U[j + 1])
            c1 = min(max(c1, c0 + 1), w)
            c0 = c1 - 1 if c1 - c0 < 1 else c0
            area = (r1 - r0) * (c1 - c0)
            if area <= 0:
                continue
            covered = integral[r1, c1] - integral[r0, c1] - integral[r1, c0] + integral[r0, c0]
            cov[i, j] = covered / area

    # Hysteresis: rectangles above the strong threshold are certain; weaker ones are kept only
    # when connected to a certain one. With a single threshold, one borderline rectangle
    # flipping could split the room and the smaller half was dropped (24.6 -> 17.1 m2 under
    # 5 mm of pose noise).
    strong = cov >= cfg.strong_threshold
    sel = (cov >= cfg.weak_threshold).astype(np.uint8)
    if not strong.any():
        raise ValueError("no rectangle of the line arrangement is covered by the room")
    rect_area = np.outer(np.diff(V), np.diff(U))
    # The room region is one connected area. Wall and outline lines a few cm apart running
    # through it leave stacks of thin strips; a run of strips sandwiched between selected
    # rectangles is inside the room. Without this, the room split there and the smaller piece
    # was dropped (24.4 vs 10.8 m2 under 5 mm of pose noise).
    _fill_sandwiched(sel, cov, np.diff(V), cfg)  # strips stacked along v (rows)
    _fill_sandwiched(sel.T, cov.T, np.diff(U), cfg)  # strips stacked along u (columns), in place
    n, comp = cv2.connectedComponents(sel, connectivity=4)
    # Pick the piece covering the most real room area (not the biggest rectangles: those are
    # often half outside the room and inflated a polygon to 27.5 m2 for a 21.3 m2 region).
    covered_area = cov * rect_area
    best, best_cover = 0, -1.0
    for k in range(1, n):
        part = comp == k
        if strong[part].any() and covered_area[part].sum() > best_cover:
            best, best_cover = k, float(covered_area[part].sum())
    occ = (comp == best).astype(np.uint8)

    # trace the outline: each arrangement cell becomes a 3x3 block, so contour vertices
    # land on block corners that map exactly onto line coordinates
    big = np.kron(occ, np.ones((3, 3), np.uint8))
    big = np.pad(big, 1)
    contours, _ = cv2.findContours(big, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contour = max(contours, key=cv2.contourArea)[:, 0, :] - 1  # (x, y) in block pixels

    def to_uv(px, py):
        j, sj = divmod(int(px), 3)
        i, si = divmod(int(py), 3)
        return (U[j] if sj == 0 else U[j + 1]), (V[i] if si == 0 else V[i + 1])

    pts = [to_uv(x, y) for x, y in contour]
    pts = _dedupe(pts)
    corners = np.array(pts)

    edges = []
    for k in range(len(corners)):
        a, b = corners[k], corners[(k + 1) % len(corners)]
        if abs(a[0] - b[0]) < 1e-9:  # constant u
            coord, axis, span = a[0], "u", (min(a[1], b[1]), max(a[1], b[1]))
            group = u_groups[int(np.argmin(np.abs(U - coord)))]
        else:
            coord, axis, span = a[1], "v", (min(a[0], b[0]), max(a[0], b[0]))
            group = v_groups[int(np.argmin(np.abs(V - coord)))]
        edges.append(Edge(axis, float(coord), (float(span[0]), float(span[1])), list(group)))
    return corners, edges


def _fill_sandwiched(sel: np.ndarray, cov: np.ndarray, sizes: np.ndarray, cfg: PolygonConfig) -> None:
    """Select runs of unselected rows (per column) whose total size is small and that have
    selected rectangles directly before and after them. Works in place (also on a transposed view)."""
    rows, cols = sel.shape
    for j in range(cols):
        i = 1
        while i < rows - 1:
            if sel[i, j] or not sel[i - 1, j]:
                i += 1
                continue
            k, total = i, 0.0
            while k < rows and not sel[k, j] and total + sizes[k] <= cfg.max_strip_m:
                total += sizes[k]
                k += 1
            if k < rows and sel[k, j] and k > i and cov[i:k, j].max() >= cfg.bridge_threshold:
                sel[i:k, j] = 1
            i = max(k, i + 1)


def _dedupe(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Remove repeated and collinear vertices from a closed rectilinear outline."""
    out = []
    for p in pts:
        if not out or (abs(p[0] - out[-1][0]) > 1e-9 or abs(p[1] - out[-1][1]) > 1e-9):
            out.append(p)
    if len(out) > 1 and abs(out[0][0] - out[-1][0]) < 1e-9 and abs(out[0][1] - out[-1][1]) < 1e-9:
        out.pop()
    changed = True
    while changed and len(out) > 4:
        changed = False
        for k in range(len(out)):
            a, b, c = out[k - 1], out[k], out[(k + 1) % len(out)]
            if (abs(a[0] - b[0]) < 1e-9 and abs(b[0] - c[0]) < 1e-9) or \
               (abs(a[1] - b[1]) < 1e-9 and abs(b[1] - c[1]) < 1e-9):
                out.pop(k)
                changed = True
                break
    return out


def polygon_area(pts: np.ndarray) -> float:
    x, y = pts[:, 0], pts[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2)
