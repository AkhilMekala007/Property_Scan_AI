"""C1 Ingest: work out what kind of capture a folder holds.

The tier is decided from the files alone; the user is never asked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from scan.core.types import Tier

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif"}
VIDEO_EXTS = {".mov", ".mp4", ".m4v"}
STRAY_REQUIRED = ("rgb.mp4", "odometry.csv", "camera_matrix.csv")

EXPECTED_LAYOUTS = (
    "a Stray Scanner export (rgb.mp4, odometry.csv, camera_matrix.csv, depth/), "
    "a folder with exactly one video file, "
    "or one folder of photos per room"
)


class CaptureFormatError(ValueError):
    """The folder does not look like any supported capture."""


@dataclass
class DetectedCapture:
    root: Path
    capture_id: str
    tier: Tier
    source_format: str
    video_path: Path | None = None
    room_dirs: dict[str, Path] = field(default_factory=dict)  # photo tier
    warnings: list[str] = field(default_factory=list)


def _visible(entries):
    return [p for p in entries if not p.name.startswith((".", "_"))]


def _images_in(folder: Path) -> list[Path]:
    return sorted(
        p for p in _visible(folder.iterdir()) if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    )


def _is_stray(folder: Path) -> bool:
    return all((folder / name).is_file() for name in STRAY_REQUIRED) and (folder / "depth").is_dir()


def _resolve_root(path: Path) -> Path:
    """Step into single wrapper folders, e.g. ``single_room/`` -> ``single_room/c00a170fe1/``."""
    current = path
    for _ in range(3):
        if _is_stray(current):
            return current
        entries = _visible(current.iterdir())
        dirs = [p for p in entries if p.is_dir()]
        files = [p for p in entries if p.is_file()]
        if len(dirs) == 1 and not files:
            current = dirs[0]
            continue
        break
    return current


def detect_capture(path: str | Path) -> DetectedCapture:
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise CaptureFormatError(f"capture folder not found: {path}")
    if path.is_file():
        if path.suffix.lower() in VIDEO_EXTS:
            return DetectedCapture(
                root=path.parent,
                capture_id=path.stem,
                tier=Tier.VIDEO,
                source_format="video_file",
                video_path=path,
            )
        raise CaptureFormatError(f"{path.name} is a file; expected {EXPECTED_LAYOUTS}")

    root = _resolve_root(path)
    capture_id = root.name

    if _is_stray(root):
        return DetectedCapture(
            root=root, capture_id=capture_id, tier=Tier.LIDAR, source_format="stray_scanner"
        )

    entries = _visible(root.iterdir())
    videos = sorted(p for p in entries if p.is_file() and p.suffix.lower() in VIDEO_EXTS)
    if len(videos) == 1:
        return DetectedCapture(
            root=root,
            capture_id=capture_id,
            tier=Tier.VIDEO,
            source_format="video_file",
            video_path=videos[0],
        )
    if len(videos) > 1:
        names = ", ".join(v.name for v in videos)
        raise CaptureFormatError(
            f"found {len(videos)} videos in {root} ({names}); one capture must be one video"
        )

    room_dirs = {d.name: d for d in sorted(entries) if d.is_dir() and _images_in(d)}
    if room_dirs:
        detected = DetectedCapture(
            root=root,
            capture_id=capture_id,
            tier=Tier.PHOTO,
            source_format="photo_folders",
            room_dirs=room_dirs,
        )
        for name, folder in room_dirs.items():
            n = len(_images_in(folder))
            if n < 2:
                detected.warnings.append(f"room '{name}' has {n} photo; at least 2 are needed")
        loose = _images_in(root)
        if loose:
            detected.warnings.append(
                f"{len(loose)} photos sit outside any room folder and are ignored"
            )
        return detected

    loose = _images_in(root)
    if loose:
        return DetectedCapture(
            root=root,
            capture_id=capture_id,
            tier=Tier.PHOTO,
            source_format="photo_folders",
            room_dirs={root.name: root},
            warnings=[
                "photos are not split into per-room folders; treating them as a single room"
            ],
        )

    raise CaptureFormatError(f"unrecognised capture in {root}; expected {EXPECTED_LAYOUTS}")
