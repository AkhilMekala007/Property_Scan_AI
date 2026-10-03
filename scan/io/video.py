"""Video helpers. Decoding is always one sequential pass: seeking in mp4 is slow."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    fps: float
    n_frames: int  # container's reported count; can be off by a frame or two


def probe(video_path: Path) -> VideoInfo:
    cap = cv2.VideoCapture(str(video_path))
    try:
        if not cap.isOpened():
            raise OSError(f"cannot open video {video_path}")
        return VideoInfo(
            width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            fps=float(cap.get(cv2.CAP_PROP_FPS)),
            n_frames=int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
        )
    finally:
        cap.release()


def _source_signature(video_path: Path) -> dict:
    st = video_path.stat()
    return {"video": video_path.name, "size": st.st_size, "mtime_ns": st.st_mtime_ns}


def extract_frames(
    video_path: Path,
    indices: list[int],
    out_dir: Path,
    jpeg_quality: int = 95,
) -> dict[int, Path]:
    """Write the requested frames as JPEGs in one pass; reuse the cache when it matches.

    Returns {frame index: jpeg path}. Raises if an index is past the end of the video.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    wanted = sorted(set(indices))
    paths = {i: out_dir / f"{i:06d}.jpg" for i in wanted}
    signature = _source_signature(video_path) | {"indices": wanted, "jpeg_quality": jpeg_quality}

    if manifest_path.exists():
        try:
            cached = json.loads(manifest_path.read_text())
        except json.JSONDecodeError:
            cached = None
        if cached == signature and all(p.exists() for p in paths.values()):
            return paths

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise OSError(f"cannot open video {video_path}")
    try:
        remaining = set(wanted)
        last = wanted[-1] if wanted else -1
        index = 0
        while remaining and index <= last:
            if index in remaining:
                ok, frame = cap.read()
                if not ok:
                    break
                cv2.imwrite(str(paths[index]), frame, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
                remaining.discard(index)
            elif not cap.grab():
                break
            index += 1
    finally:
        cap.release()

    if remaining:
        raise ValueError(
            f"{video_path.name} ended before frame {min(remaining)} "
            f"({len(remaining)} requested frames missing)"
        )
    manifest_path.write_text(json.dumps(signature))
    return paths


def read_rgb(path: Path):
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise OSError(f"cannot read image {path}")
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
