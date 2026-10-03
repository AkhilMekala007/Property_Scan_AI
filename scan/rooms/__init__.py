"""C6 Room segmentation: split one continuous capture into rooms and doorways."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

from scan.core.types import Trajectory
from scan.rooms.floormap import FloorMap, FloorMapConfig, build_floor_map
from scan.rooms.segment import (
    SegmentConfig,
    drop_small_rooms,
    find_contacts,
    grow,
    is_corridor,
    seed_regions,
)
from scan.structure import StructureModel

__all__ = ["Room", "Doorway", "RoomLayout", "segment_rooms", "write_rooms", "render_rooms"]

CEILING_MATCH_M = 0.05


@dataclass
class Room:
    id: int
    name: str
    kind: str  # "room" | "corridor"
    area_m2: float  # rough: floor-map cells; C7b measures the precise area from wall planes
    centroid_xz: tuple[float, float]
    outline_xz: list[tuple[float, float]]
    ceiling_level: int | None  # index into StructureModel.ceilings
    ceiling_height_m: float | None
    ceiling_note: str | None
    wall_ids: list[int]
    doorway_ids: list[int] = field(default_factory=list)


@dataclass
class Doorway:
    id: int
    room_a: int
    room_b: int
    kind: str  # "door" (door-labelled frame nearby) | "opening"
    center_xz: tuple[float, float]
    width_m: float  # rough; C8 measures openings precisely


@dataclass
class RoomLayout:
    capture_id: str
    manhattan_deg: float
    cell_m: float
    rooms: list[Room]
    doorways: list[Doorway]
    unreached_areas_m2: list[float] = field(default_factory=list)  # seen only from outside, dropped
    floor_map: FloorMap | None = field(default=None, repr=False)
    labels: np.ndarray | None = field(default=None, repr=False)  # room id per cell, 0 = outside

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("floor_map", None)
        d.pop("labels", None)
        return d


def _assign_ceiling(model: StructureModel, fmap: FloorMap, mask: np.ndarray, centroid_xz):
    ys = fmap.ceiling_y[mask]
    ys = ys[np.isfinite(ys)]
    if not model.ceilings:
        return None, None, model.ceiling_note or "ceiling not observed"
    if len(ys) < 20:
        return None, None, "ceiling not observed in this room"
    cx, cz = centroid_xz
    votes = np.zeros(len(model.ceilings), int)
    for i, level in enumerate(model.ceilings):
        votes[i] = int(np.sum(np.abs(ys - level.y_at(cx, cz)) < CEILING_MATCH_M))
    if votes.max() < 20:
        return None, None, "ceiling seen but matches no fitted ceiling level"
    best = int(np.argmax(votes))
    height = model.ceilings[best].y_at(cx, cz) - model.floor.y_at(cx, cz)
    return best, float(height), None


def _walls_of(model: StructureModel, fmap: FloorMap, labels: np.ndarray) -> dict[int, list[int]]:
    """A wall belongs to the room its inward normal points into."""
    out: dict[int, list[int]] = {}
    frame = fmap.frame
    for wall in model.walls:
        a, b = np.array(wall.start_xz), np.array(wall.end_xz)
        samples = a + np.linspace(0.1, 0.9, 9)[:, None] * (b - a) + 0.15 * np.array(wall.normal_xz)
        rc = frame.xz_to_cell(samples)
        rc = rc[frame.in_bounds(rc)]
        ids = labels[rc[:, 0], rc[:, 1]]
        ids = ids[ids > 0]
        if len(ids):
            vals, counts = np.unique(ids, return_counts=True)
            if counts.max() >= 3:
                out.setdefault(int(vals[np.argmax(counts)]), []).append(wall.id)
    return out


def segment_rooms(model: StructureModel, trajectory: Trajectory | None,
                  map_cfg: FloorMapConfig | None = None,
                  seg_cfg: SegmentConfig | None = None) -> RoomLayout:
    map_cfg = map_cfg or FloorMapConfig()
    seg_cfg = seg_cfg or SegmentConfig()
    fmap = build_floor_map(model, trajectory, map_cfg)
    cell = fmap.frame.cell_m

    labels = grow(seed_regions(fmap, seg_cfg), fmap.inside)
    labels = drop_small_rooms(labels, cell, seg_cfg.min_room_m2)
    labels, unreached = _drop_unreached(labels, find_contacts(labels, cell, seg_cfg.min_doorway_m),
                                        fmap, trajectory, cell)
    contacts = find_contacts(labels, cell, seg_cfg.min_doorway_m)
    walls_by_room = _walls_of(model, fmap, labels)
    door_near = cv2.dilate(fmap.door.astype(np.uint8), np.ones((7, 7), np.uint8)).astype(bool)

    rooms: list[Room] = []
    for rid in range(1, int(labels.max()) + 1):
        mask = labels == rid
        rc = np.column_stack(np.nonzero(mask))
        centroid = tuple(float(v) for v in fmap.frame.cell_to_xz(rc.mean(axis=0))[0])
        contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contour = max(contours, key=cv2.contourArea)
        poly = cv2.approxPolyDP(contour, 2.0, True)[:, 0, :]  # (col, row)
        outline = fmap.frame.cell_to_xz(poly[:, ::-1])
        level, height, note = _assign_ceiling(model, fmap, mask, centroid)
        rooms.append(Room(
            id=rid,
            name=f"room_{rid}",
            kind="room",
            area_m2=round(float(mask.sum() * cell * cell), 2),
            centroid_xz=centroid,
            outline_xz=[(round(float(x), 4), round(float(z), 4)) for x, z in outline],
            ceiling_level=level,
            ceiling_height_m=None if height is None else round(height, 4),
            ceiling_note=note,
            wall_ids=sorted(walls_by_room.get(rid, [])),
        ))

    doorways: list[Doorway] = []
    for k, c in enumerate(contacts):
        centre = fmap.frame.cell_to_xz(c.cells.mean(axis=0))[0]
        kind = "door" if door_near[c.cells[:, 0], c.cells[:, 1]].any() else "opening"
        doorways.append(Doorway(k, c.room_a, c.room_b, kind,
                                (float(centre[0]), float(centre[1])), round(c.width_m, 2)))
        rooms[c.room_a - 1].doorway_ids.append(k)
        rooms[c.room_b - 1].doorway_ids.append(k)

    for room in rooms:
        if len(room.doorway_ids) >= 2 and is_corridor(labels == room.id, seg_cfg.corridor_aspect):
            room.kind = "corridor"
            room.name = f"corridor_{room.id}"

    return RoomLayout(model.capture_id, model.manhattan_deg, cell, rooms, doorways,
                      unreached_areas_m2=unreached, floor_map=fmap, labels=labels)


def _drop_unreached(labels: np.ndarray, contacts, fmap: FloorMap, trajectory: Trajectory | None,
                    cell: float) -> tuple[np.ndarray, list[float]]:
    """Remove regions with no doorway and no walked path: areas seen only from outside
    (through a window or an open door). The walk is continuous, so every real room either
    contains part of the path or touches a room that does."""
    if trajectory is None or labels.max() <= 1:
        return labels, []
    rc = fmap.frame.xz_to_cell(trajectory.positions[:, [0, 2]])
    rc = rc[fmap.frame.in_bounds(rc)]
    walked = set(np.unique(labels[rc[:, 0], rc[:, 1]]).tolist()) - {0}
    connected = {c.room_a for c in contacts} | {c.room_b for c in contacts}
    dropped = []
    out = labels.copy()
    for rid in range(1, int(labels.max()) + 1):
        if rid not in walked and rid not in connected:
            dropped.append(round(float((labels == rid).sum() * cell * cell), 2))
            out[labels == rid] = 0
    if not dropped:
        return labels, []
    return drop_small_rooms(out, cell, 0.0), dropped  # renumber 1..N


def write_rooms(layout: RoomLayout, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "rooms.json"
    path.write_text(json.dumps(layout.to_dict(), indent=2), encoding="utf-8")
    return path


_COLOURS = [(242, 166, 90), (125, 196, 230), (170, 220, 130), (230, 150, 200), (250, 220, 110),
            (160, 160, 235), (120, 210, 190), (235, 135, 120), (200, 180, 150), (180, 230, 230)]


def render_rooms(layout: RoomLayout, out_path: Path, scale: int = 2) -> Path:
    """Room-aligned top-down: each room filled in its own colour, walls black, doorways marked."""
    fmap, labels = layout.floor_map, layout.labels
    h, w = labels.shape
    img = np.full((h, w, 3), 255, np.uint8)
    for room in layout.rooms:
        img[labels == room.id] = _COLOURS[(room.id - 1) % len(_COLOURS)]
    img[fmap.barrier] = (40, 40, 40)
    img = cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_NEAREST)

    def px(xz):
        rc = fmap.frame.xz_to_cell(np.array([xz]))[0]
        return int((rc[1] + 0.5) * scale), int((rc[0] + 0.5) * scale)

    for d in layout.doorways:
        colour = (0, 0, 200) if d.kind == "door" else (0, 140, 0)
        cv2.circle(img, px(d.center_xz), 6, colour, -1)
        cv2.putText(img, f"{d.width_m:.2f}", (px(d.center_xz)[0] + 7, px(d.center_xz)[1] - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, colour, 1)
    for room in layout.rooms:
        x, y = px(room.centroid_xz)
        ceiling = f"h {room.ceiling_height_m:.2f}" if room.ceiling_height_m else "h n/a"
        for k, text in enumerate((room.name, f"{room.area_m2:.1f} m2", ceiling)):
            cv2.putText(img, text, (x - 30, y + 14 * k), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0), 1)
    cv2.putText(img, "red = door, green = opening (width m)", (8, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                (0, 0, 0), 1)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), img)
    return out_path
