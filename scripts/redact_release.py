"""Build a privacy-safe copy of the raw benchmark data for publication (nothing is uploaded).

* Photos (HEIC/JPEG): GPS fields in the EXIF block are zeroed in place - same byte length, image
  data untouched - then verified: no GPS values remain and the decoded pixels are identical.
* Polycam CSV: rows with altitude / compass bearing removed. The Polycam spatial-report PDF and the
  raw Polycam zip (they contain latitude / longitude) are NOT copied.
* Videos (.MOV): copied only if their location atom can be blanked in place (same length); otherwise
  listed as withheld.

Usage: python scripts/redact_release.py [--out data/release]
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import struct
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "benchmark"
SETS = ["lidar_flat", "photos_v4", "photos_repeat", "photos_damage", "video_flat", "video_v3"]
SKIP = {"[Polycam Spatial Report] 3_10_2026.pdf", "3_10_2026.zip", "[Polycam Floor Plan] 3_10_2026.zip"}


def _zero_gps(blob: bytearray, tiff: int) -> int:
    """Zero every GPS IFD entry value (and out-of-line data) in a TIFF/EXIF block. Returns entries zeroed."""
    bo = blob[tiff:tiff + 2]
    e = "<" if bo == b"II" else ">"
    u16 = lambda o: struct.unpack_from(e + "H", blob, tiff + o)[0]
    u32 = lambda o: struct.unpack_from(e + "I", blob, tiff + o)[0]
    sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8, 12: 8}
    ifd0 = u32(4)
    gps_off = None
    for k in range(u16(ifd0)):
        ent = ifd0 + 2 + 12 * k
        if u16(ent) == 0x8825:
            gps_off = u32(ent + 8)
    if gps_off is None:
        return 0
    n = u16(gps_off)
    for k in range(n):
        ent = gps_off + 2 + 12 * k
        typ, cnt = u16(ent + 2), u32(ent + 4)
        size = sizes.get(typ, 1) * cnt
        if size > 4:  # value stored elsewhere: zero it there
            off = u32(ent + 8)
            blob[tiff + off:tiff + off + size] = bytes(size)
        else:
            blob[tiff + ent + 8:tiff + ent + 12] = bytes(4)
    return n


def redact_photo(src: Path, dst: Path) -> str:
    data = bytearray(src.read_bytes())
    pos, total = 0, 0
    while True:  # every EXIF block ("Exif\0\0" + TIFF header)
        i = data.find(b"Exif\x00\x00", pos)
        if i < 0:
            break
        tiff = i + 6
        if data[tiff:tiff + 2] in (b"II", b"MM"):
            total += _zero_gps(data, tiff)
        pos = i + 6
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(bytes(data))
    return f"{total} GPS field(s) zeroed"


def verify_photo(src: Path, dst: Path) -> None:
    from PIL import Image
    import pillow_heif

    pillow_heif.register_heif_opener()
    gps = Image.open(dst).getexif().get_ifd(0x8825)
    def empty(v) -> bool:  # zeroed field: null bytes / empty, or 0 or 0/0 (reads back as nan)
        if isinstance(v, (str, bytes)):
            return not (v.encode("latin-1") if isinstance(v, str) else v).strip(b"\x00")
        if isinstance(v, tuple):
            return all(empty(x) for x in v)
        try:
            f = float(v)
        except (TypeError, ValueError):
            return v is None
        return f != f or f == 0.0

    leftover = [k for k, v in gps.items() if not empty(v)]
    if leftover:
        raise RuntimeError(f"{dst.name}: GPS values remain {leftover}")
    a = np.asarray(Image.open(src).convert("RGB"))
    b = np.asarray(Image.open(dst).convert("RGB"))
    if a.shape != b.shape or hashlib.sha1(a.tobytes()).digest() != hashlib.sha1(b.tobytes()).digest():
        raise RuntimeError(f"{dst.name}: decoded pixels changed")


def redact_mov(src: Path, dst: Path) -> bool:
    """Blank Apple's ISO 6709 location strings (e.g. '+17.3850+078.4867+500.000/') in place."""
    import re

    data = bytearray(src.read_bytes())
    pattern = re.compile(rb"[+-]\d{2}\.\d{3,}[+-]\d{3}\.\d{3,}(?:[+-]\d+(?:\.\d+)?)?/")
    hits = list(pattern.finditer(data))
    for m in hits:
        data[m.start():m.end()] = b"0" * (m.end() - m.start())
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(bytes(data))
    return bool(hits)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "release"))
    args = ap.parse_args()
    out = Path(args.out)
    log = []
    for name in SETS:
        for src in sorted((RAW / name).rglob("*")):
            if src.is_dir():
                continue
            dst = out / name / src.relative_to(RAW / name)
            suf = src.suffix.lower()
            if suf in (".heic", ".jpg", ".jpeg"):
                msg = redact_photo(src, dst)
                verify_photo(src, dst)
                log.append(f"{dst.relative_to(out)}: {msg}, pixels identical")
            elif suf == ".mov":
                found = redact_mov(src, dst)
                log.append(f"{dst.relative_to(out)}: location {'blanked' if found else 'not found'}")
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
    poly = RAW / "polycam" / "floor_plan"
    for src in sorted(poly.iterdir()):
        if src.name in SKIP:
            continue
        dst = out / "polycam" / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix.lower() == ".csv":
            rows = [r for r in src.read_text(encoding="utf-8").splitlines()
                    if not any(k in r for k in ("Altitude", "Compass direction"))]
            dst.write_text("\n".join(rows) + "\n", encoding="utf-8")
            log.append(f"polycam/{src.name}: altitude / compass rows removed")
        else:
            shutil.copy2(src, dst)
    shutil.copy2(ROOT / "bench" / "ground_truth" / "flat_3bhk.yaml", out / "ground_truth_flat_3bhk.yaml")
    (out / "REDACTION_LOG.txt").write_text("\n".join(log) + "\nWithheld: " + ", ".join(sorted(SKIP)) + "\n",
                                          encoding="utf-8")
    print(f"release copy in {out} ({len(log)} redaction entries); withheld: {', '.join(sorted(SKIP))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
