"""Our surface classes and the mapping from ADE20K's 150 classes.

Mapping is by class *name* (from the model's config), not by id, so a different
ADE20K-trained model can be swapped in without touching this table.
"""

from __future__ import annotations

import enum

import numpy as np


class Surface(enum.IntEnum):
    OTHER = 0  # furniture, objects, anything not listed below
    WALL = 1
    FLOOR = 2
    CEILING = 3
    DOOR = 4
    WINDOW = 5
    MIRROR = 6
    PERSON = 7


# ADE20K name -> our class. Pictures and posters sit on the wall plane, so they count as wall.
# Note: ADE20K "glass" is a drinking glass, not window glass, so it stays OTHER.
ADE_TO_SURFACE: dict[str, Surface] = {
    "wall": Surface.WALL,
    "column": Surface.WALL,
    "painting": Surface.WALL,
    "poster": Surface.WALL,
    "bulletin board": Surface.WALL,
    "floor": Surface.FLOOR,
    "rug": Surface.FLOOR,
    "ceiling": Surface.CEILING,
    "door": Surface.DOOR,
    "screen door": Surface.DOOR,
    "windowpane": Surface.WINDOW,
    "curtain": Surface.WINDOW,
    "blind": Surface.WINDOW,
    "mirror": Surface.MIRROR,
    "person": Surface.PERSON,
}

MAPPING_VERSION = 1  # bump when the table changes, so cached labels are recomputed

COLOURS_RGB: dict[Surface, tuple[int, int, int]] = {
    Surface.OTHER: (128, 128, 128),
    Surface.WALL: (70, 130, 220),
    Surface.FLOOR: (230, 160, 40),
    Surface.CEILING: (60, 190, 160),
    Surface.DOOR: (200, 60, 60),
    Surface.WINDOW: (160, 90, 220),
    Surface.MIRROR: (240, 240, 60),
    Surface.PERSON: (240, 90, 180),
}


def build_lookup(id2label: dict[int, str]) -> np.ndarray:
    """Array mapping model class id -> Surface value."""
    lookup = np.full(max(id2label) + 1, Surface.OTHER, dtype=np.uint8)
    for idx, name in id2label.items():
        lookup[idx] = ADE_TO_SURFACE.get(name.strip().lower(), Surface.OTHER)
    return lookup


def colourise(labels: np.ndarray) -> np.ndarray:
    palette = np.zeros((len(Surface), 3), np.uint8)
    for s, rgb in COLOURS_RGB.items():
        palette[s] = rgb
    return palette[labels]
