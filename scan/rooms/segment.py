"""Split the floor map into rooms and find the doorways between them."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import cv2
import numpy as np

from scan.rooms.floormap import FloorMap, _disk

NEIGHBOURS_4 = ((1, 0), (-1, 0), (0, 1), (0, -1))


@dataclass(frozen=True)
class SegmentConfig:
    door_cut_m: float = 0.15  # floor this close to door-labelled voxels is cut when seeding
    neck_m: float = 0.30  # erosion radius: passages narrower than 2x this split rooms
    min_room_m2: float = 0.8
    min_doorway_m: float = 0.25  # rooms only touch through wall gaps; 0.4 was knife-edge (frames narrow contacts to ~0.4 m)
    corridor_aspect: float = 2.5


def seed_regions(fmap: FloorMap, cfg: SegmentConfig) -> np.ndarray:
    """Integer seed labels (0 = none): the floor map cut at doors and narrow necks."""
    cell = fmap.frame.cell_m
    cut = fmap.inside.copy()
    r_door = max(1, int(round(cfg.door_cut_m / cell)))
    cut &= ~cv2.dilate(fmap.door.astype(np.uint8), _disk(r_door)).astype(bool)
    r_neck = max(1, int(round(cfg.neck_m / cell)))
    core = cv2.erode(cut.astype(np.uint8), _disk(r_neck))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(core, connectivity=8)
    min_cells = cfg.min_room_m2 / (cell * cell)
    seeds = np.zeros_like(labels)
    # keep components big enough to be rooms, numbered largest first (deterministic)
    order = sorted((i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= min_cells * 0.25),
                   key=lambda i: (-stats[i, cv2.CC_STAT_AREA], i))
    for new_id, old in enumerate(order, start=1):
        seeds[labels == old] = new_id
    return seeds


def grow(seeds: np.ndarray, inside: np.ndarray) -> np.ndarray:
    """Assign every inside cell to the geodesically nearest seed (multi-source BFS)."""
    labels = np.where(inside, seeds, 0).astype(np.int32)
    h, w = labels.shape
    queue = deque(zip(*np.nonzero(labels)))
    while queue:
        r, c = queue.popleft()
        lab = labels[r, c]
        for dr, dc in NEIGHBOURS_4:
            rr, cc = r + dr, c + dc
            if 0 <= rr < h and 0 <= cc < w and inside[rr, cc] and labels[rr, cc] == 0:
                labels[rr, cc] = lab
                queue.append((rr, cc))
    return labels


def drop_small_rooms(labels: np.ndarray, cell_m: float, min_m2: float) -> np.ndarray:
    """Merge rooms below ``min_m2`` into the neighbour they share most boundary with."""
    while True:
        ids, counts = np.unique(labels[labels > 0], return_counts=True)
        small = [(i, n) for i, n in zip(ids, counts) if n * cell_m * cell_m < min_m2]
        if not small:
            break
        victim = min(small, key=lambda t: (t[1], t[0]))[0]
        mask = labels == victim
        ring = cv2.dilate(mask.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool) & ~mask
        neighbours, n = np.unique(labels[ring & (labels > 0)], return_counts=True)
        labels[mask] = neighbours[np.argmax(n)] if len(neighbours) else 0
    # renumber 1..N by size, largest first
    ids, counts = np.unique(labels[labels > 0], return_counts=True)
    out = np.zeros_like(labels)
    for new_id, old in enumerate(ids[np.argsort(-counts, kind="stable")], start=1):
        out[labels == old] = new_id
    return out


@dataclass
class Contact:
    room_a: int
    room_b: int
    cells: np.ndarray  # (N, 2) row/col of boundary cells on room_a's side
    width_m: float


def find_contacts(labels: np.ndarray, cell_m: float, min_width_m: float) -> list[Contact]:
    """Places where two rooms touch directly (not through a wall): the doorways."""
    h, w = labels.shape
    pairs: dict[tuple[int, int], set[tuple[int, int]]] = {}
    for dr, dc in ((1, 0), (0, 1)):
        a = labels[: h - dr, : w - dc]
        b = labels[dr:, dc:]
        touch = (a > 0) & (b > 0) & (a != b)
        for r, c in zip(*np.nonzero(touch)):
            la, lb = int(a[r, c]), int(b[r, c])
            key = (min(la, lb), max(la, lb))
            pairs.setdefault(key, set()).add((r, c) if la == key[0] else (r + dr, c + dc))

    contacts = []
    for (ra, rb), cells in sorted(pairs.items()):
        mask = np.zeros_like(labels, np.uint8)
        rc = np.array(sorted(cells))
        mask[rc[:, 0], rc[:, 1]] = 1
        mask = cv2.dilate(mask, np.ones((3, 3), np.uint8))  # join diagonal steps
        n, comp = cv2.connectedComponents(mask, connectivity=8)
        for k in range(1, n):
            part = rc[comp[rc[:, 0], rc[:, 1]] == k]
            if len(part) == 0:
                continue
            extent = part.max(axis=0) - part.min(axis=0) + 1
            width = float(max(extent) * cell_m)
            if width >= min_width_m:
                contacts.append(Contact(ra, rb, part, width))
    return contacts


def is_corridor(mask: np.ndarray, aspect: float) -> bool:
    pts = np.column_stack(np.nonzero(mask)).astype(np.float32)
    if len(pts) < 10:
        return False
    (_, _), (w, h), _ = cv2.minAreaRect(pts)
    short, long = sorted((w, h))
    return short > 0 and long / short >= aspect
