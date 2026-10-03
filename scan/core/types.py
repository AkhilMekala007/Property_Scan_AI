"""Shared capture format.

Every tier adapter converts its raw files into a ``FrameSet``; everything
downstream of the adapters depends only on the types in this module.

Conventions (fixed for the whole project):

* Units are metres and seconds.
* Camera frame is OpenCV style: +x right, +y down, +z forward.
* World frame is gravity aligned with +y up (ARKit world), origin wherever
  the capture app put it.
* ``Frame.T_world_cam`` is a 4x4 matrix mapping camera points to world
  points: ``p_world = T_world_cam @ [p_cam, 1]``.
* Invalid depth is ``NaN``, never 0.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


class Tier(str, enum.Enum):
    LIDAR = "lidar"
    VIDEO = "video"
    PHOTO = "photo"


@dataclass(frozen=True)
class Intrinsics:
    """Pinhole intrinsics at a specific image resolution (pixel centres at integers)."""

    fx: float
    fy: float
    cx: float
    cy: float
    width: int
    height: int

    def scaled_to(self, width: int, height: int) -> Intrinsics:
        """Intrinsics for the same camera resampled to ``width`` x ``height``."""
        sx = width / self.width
        sy = height / self.height
        return Intrinsics(
            fx=self.fx * sx,
            fy=self.fy * sy,
            cx=(self.cx + 0.5) * sx - 0.5,
            cy=(self.cy + 0.5) * sy - 0.5,
            width=width,
            height=height,
        )

    def matrix(self) -> np.ndarray:
        return np.array(
            [[self.fx, 0.0, self.cx], [0.0, self.fy, self.cy], [0.0, 0.0, 1.0]]
        )


@dataclass
class CaptureMeta:
    capture_id: str
    tier: Tier
    source_format: str  # "stray_scanner" | "video_file" | "photo_folders"
    source_dir: Path
    device_model: str | None = None
    n_frames_raw: int = 0
    duration_s: float | None = None
    warnings: list[str] = field(default_factory=list)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


ArrayLoader = Callable[[], np.ndarray]


@dataclass
class Frame:
    """One keyframe. Pixel data is loaded on demand so a FrameSet stays small."""

    index: int  # index in the raw capture, not in the keyframe list
    timestamp: float
    intrinsics: Intrinsics  # at RGB resolution
    T_world_cam: np.ndarray | None = None  # None when poses are unknown (photo tier)
    room_hint: str | None = None  # photo tier: folder name
    depth_size: tuple[int, int] | None = None  # (width, height) of depth maps
    rgb_ok: bool = True  # False when QC found the image blurry; depth may still be fine
    _rgb: ArrayLoader | None = field(default=None, repr=False)
    _depth: ArrayLoader | None = field(default=None, repr=False)
    _confidence: ArrayLoader | None = field(default=None, repr=False)
    _depth_sigma: ArrayLoader | None = field(default=None, repr=False)

    @property
    def has_depth(self) -> bool:
        return self._depth is not None

    @property
    def depth_intrinsics(self) -> Intrinsics | None:
        if self.depth_size is None:
            return None
        return self.intrinsics.scaled_to(*self.depth_size)

    def rgb(self) -> np.ndarray:
        """HxWx3 uint8, RGB channel order."""
        if self._rgb is None:
            raise ValueError(f"frame {self.index} has no RGB image")
        return self._rgb()

    def depth(self) -> np.ndarray | None:
        """HxW float32 metres at depth resolution, NaN where invalid."""
        return None if self._depth is None else self._depth()

    def confidence(self) -> np.ndarray | None:
        """HxW uint8 sensor confidence (Stray/ARKit: 0 low, 1 medium, 2 high)."""
        return None if self._confidence is None else self._confidence()

    def depth_sigma(self) -> np.ndarray | None:
        """HxW float32 one-sigma depth uncertainty in metres, NaN where unusable."""
        return None if self._depth_sigma is None else self._depth_sigma()


@dataclass
class Trajectory:
    """Camera path at full capture rate (every raw frame, not just keyframes)."""

    timestamps: np.ndarray  # (N,) seconds
    positions: np.ndarray  # (N, 3) world metres
    rotations: np.ndarray  # (N, 3, 3) camera-to-world

    def __len__(self) -> int:
        return len(self.timestamps)


@dataclass
class FrameSet:
    meta: CaptureMeta
    frames: list[Frame]
    cache_dir: Path | None = None
    trajectory: Trajectory | None = None  # None when poses are unknown

    def with_frames(self, frames: list[Frame]) -> FrameSet:
        """Same capture, different frame selection."""
        return FrameSet(self.meta, frames, self.cache_dir, self.trajectory)

    def __len__(self) -> int:
        return len(self.frames)

    @property
    def has_poses(self) -> bool:
        return bool(self.frames) and all(f.T_world_cam is not None for f in self.frames)

    @property
    def has_depth(self) -> bool:
        return bool(self.frames) and all(f.has_depth for f in self.frames)
