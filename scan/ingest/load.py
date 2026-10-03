"""Entry point: capture folder -> FrameSet."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from scan.core.types import FrameSet
from scan.ingest.detect import detect_capture

if TYPE_CHECKING:
    from scan.adapters.base import AdapterConfig

DEFAULT_CACHE_ROOT = Path("outputs") / "cache"


def load_capture(
    path: str | Path,
    cache_root: str | Path = DEFAULT_CACHE_ROOT,
    config: AdapterConfig | None = None,
) -> FrameSet:
    from scan.adapters import get_adapter  # deferred: adapters import ingest types
    from scan.adapters.base import AdapterConfig

    detected = detect_capture(path)
    adapter = get_adapter(detected.source_format)
    frameset = adapter.load(detected, Path(cache_root), config or AdapterConfig())
    frameset.meta.warnings[:0] = detected.warnings
    return frameset
