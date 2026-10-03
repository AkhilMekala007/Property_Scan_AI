"""Registry of tier adapters, keyed by ``DetectedCapture.source_format``."""

from __future__ import annotations

from scan.adapters.base import Adapter
from scan.adapters.stray import StrayScannerAdapter


class _NotYetImplemented:
    def __init__(self, source_format: str):
        self.source_format = source_format

    def load(self, detected, cache_root, config):
        raise NotImplementedError(
            f"the '{self.source_format}' adapter ({detected.tier.value} tier) is not implemented yet"
        )


_ADAPTERS: dict[str, Adapter] = {
    "stray_scanner": StrayScannerAdapter(),
    "video_file": _NotYetImplemented("video_file"),
    "photo_folders": _NotYetImplemented("photo_folders"),
}


def get_adapter(source_format: str) -> Adapter:
    try:
        return _ADAPTERS[source_format]
    except KeyError:
        raise ValueError(f"no adapter registered for source format '{source_format}'") from None
