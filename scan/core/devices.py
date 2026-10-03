"""Device matrix: which iPhones we support and which have LiDAR."""

from __future__ import annotations

import re
from dataclasses import dataclass

MIN_SUPPORTED_GENERATION = 15
FIRST_LIDAR_GENERATION = 12  # iPhone 12 Pro and later Pro models


@dataclass(frozen=True)
class DeviceInfo:
    name: str
    generation: int | None  # 15 for "iPhone 15 Pro"; None when unparseable
    is_pro: bool
    supported: bool | None  # None when we can't tell
    has_lidar: bool | None


def lookup_device(name: str | None) -> DeviceInfo | None:
    if not name:
        return None
    text = name.strip()
    lowered = text.lower()
    is_pro = bool(re.search(r"\bpro\b", lowered))
    match = re.search(r"iphone\s*(\d{1,2})", lowered)
    if match:
        gen = int(match.group(1))
        return DeviceInfo(
            name=text,
            generation=gen,
            is_pro=is_pro,
            supported=gen >= MIN_SUPPORTED_GENERATION,
            has_lidar=is_pro and gen >= FIRST_LIDAR_GENERATION,
        )
    if re.search(r"iphone\s*air", lowered):  # released alongside the 17 series, no LiDAR
        return DeviceInfo(text, 17, False, True, False)
    return DeviceInfo(text, None, is_pro, None, None)
