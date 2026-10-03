"""Read iPhone photos (HEIC or JPEG) with orientation applied and camera intrinsics from EXIF."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

FULL_FRAME_DIAGONAL_MM = 43.27  # 35 mm film diagonal; Apple's 35 mm equivalent is diagonal based


@dataclass(frozen=True)
class PhotoInfo:
    path: Path
    width: int  # after orientation
    height: int
    focal_px: float | None  # from the 35 mm-equivalent focal length, None if not recorded
    make: str | None
    model: str | None
    lens: str | None


def _open(path: Path):
    from PIL import Image, ImageOps

    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    img = Image.open(path)
    exif = img.getexif()
    return ImageOps.exif_transpose(img), exif


def photo_info(path: Path) -> PhotoInfo:
    img, exif = _open(path)
    sub = exif.get_ifd(0x8769) if exif else {}
    w, h = img.size
    f35 = sub.get(41989)
    focal = float(f35) * float(np.hypot(w, h)) / FULL_FRAME_DIAGONAL_MM if f35 else None
    return PhotoInfo(path, w, h, focal, exif.get(271) if exif else None, exif.get(272) if exif else None,
                     sub.get(42036))


def read_photo(path: Path, max_side: int | None = None) -> np.ndarray:
    """RGB uint8, orientation applied, optionally downscaled so the long side is ``max_side``."""
    img, _ = _open(path)
    img = img.convert("RGB")
    if max_side and max(img.size) > max_side:
        s = max_side / max(img.size)
        img = img.resize((round(img.size[0] * s), round(img.size[1] * s)))
    return np.asarray(img)
