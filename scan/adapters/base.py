"""Adapter interface: one implementation per raw capture format."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from scan.core.types import FrameSet

if TYPE_CHECKING:
    from scan.ingest.detect import DetectedCapture


@dataclass(frozen=True)
class KeyframeConfig:
    min_translation_m: float = 0.10
    min_rotation_deg: float = 5.0
    max_keyframes: int = 400


@dataclass(frozen=True)
class LidarNoiseModel:
    """One-sigma LiDAR depth noise: sigma = base_m + quad_per_m * z^2, scaled by confidence.

    Placeholder values; C12 calibration replaces them with values fitted on the benchmark.
    """

    base_m: float = 0.005
    quad_per_m: float = 0.005
    medium_conf_factor: float = 3.0  # confidence == 1; confidence == 0 is unusable (NaN)


@dataclass(frozen=True)
class AdapterConfig:
    keyframes: KeyframeConfig = field(default_factory=KeyframeConfig)
    lidar_noise: LidarNoiseModel = field(default_factory=LidarNoiseModel)
    device_model: str | None = None  # user override when the files don't record it
    jpeg_quality: int = 95


class Adapter(Protocol):
    def load(self, detected: DetectedCapture, cache_root: Path, config: AdapterConfig) -> FrameSet: ...
