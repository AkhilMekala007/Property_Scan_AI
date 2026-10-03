"""Turn measurements into decisions: which frames to drop, which issues to raise."""

from __future__ import annotations

import numpy as np

from scan.core.devices import MIN_SUPPORTED_GENERATION, lookup_device
from scan.core.types import CaptureMeta
from scan.qc.report import ERROR, INFO, WARNING, Coverage, FrameQuality, Issue, QcConfig

BLURRY, FAST, TOO_DARK, BLOWN_OUT, LOW_DEPTH, NEAR_JUMP = (
    "blurry", "fast_motion", "too_dark", "blown_out", "low_depth", "near_tracking_jump"
)


def relative_sharpness(sharpness: list[float], window: int) -> np.ndarray:
    """Each frame's sharpness divided by the median of its neighbours in time.

    Neighbouring keyframes see similar content, so a low ratio means motion blur
    rather than a low-texture view (a plain wall is sharp but scores low globally).
    """
    s = np.asarray(sharpness, dtype=np.float64)
    out = np.ones_like(s)
    for i in range(len(s)):
        lo, hi = max(0, i - window), min(len(s), i + window + 1)
        ref = float(np.median(s[lo:hi]))
        out[i] = s[i] / ref if ref > 0 else 1.0
    return out


def mark_frames(
    frames: list[FrameQuality],
    jumps_s: list[float],
    cfg: QcConfig,
    depth_from_sensor: bool = False,
) -> None:
    """Fill ``kept`` / ``reasons`` / ``rgb_ok`` on every frame, in place.

    With sensor depth (LiDAR) a blurry image doesn't spoil the geometry, so the
    frame is kept and only marked ``rgb_ok = False``. When depth is estimated from
    the image itself (video / photo tiers), blur makes the frame unusable.
    """
    if not frames:
        return
    rel = relative_sharpness([f.sharpness for f in frames], cfg.sharpness_window)
    for f, r in zip(frames, rel):
        f.sharpness_rel = float(r)
        f.reasons = []
        f.rgb_ok = f.sharpness_rel >= cfg.min_sharpness_rel
        if not f.rgb_ok and not depth_from_sensor:
            f.reasons.append(BLURRY)
        if (f.speed_mps or 0) > cfg.max_speed_mps or (f.angular_dps or 0) > cfg.max_angular_dps:
            f.reasons.append(FAST)
        if f.dark_frac > cfg.max_dark_frac:
            f.reasons.append(TOO_DARK)
        if f.bright_frac > cfg.max_bright_frac:
            f.reasons.append(BLOWN_OUT)
        if f.depth_valid_frac is not None and f.depth_valid_frac < cfg.min_depth_valid_frac:
            f.reasons.append(LOW_DEPTH)
        if any(abs(f.timestamp - j) < cfg.jump_guard_s for j in jumps_s):
            f.reasons.append(NEAR_JUMP)
        f.kept = not f.reasons

    floor = max(cfg.min_keep_frames, int(np.ceil(cfg.min_keep_frac * len(frames))))
    floor = min(floor, len(frames))
    n_kept = sum(f.kept for f in frames)
    if n_kept < floor:
        # Re-admit the least-bad dropped frames: fewest problems first, then sharpest.
        dropped = sorted(
            (f for f in frames if not f.kept), key=lambda f: (len(f.reasons), -f.sharpness_rel)
        )
        for f in dropped[: floor - n_kept]:
            f.kept = True


def find_issues(
    meta: CaptureMeta,
    frames: list[FrameQuality],
    coverage: Coverage,
    jumps_s: list[float],
    median_brightness: float,
    cfg: QcConfig,
) -> list[Issue]:
    issues: list[Issue] = []
    n_in = len(frames)
    n_kept = sum(f.kept for f in frames)
    kept_with_problems = sum(1 for f in frames if f.kept and f.reasons)

    blurry_kept = sum(1 for f in frames if f.kept and not f.rgb_ok)
    if n_in and blurry_kept / n_in > 0.25:
        issues.append(Issue(
            "MANY_BLURRY_FRAMES", WARNING,
            f"{blurry_kept} of {n_in} images are blurry; geometry uses their depth, "
            "but labels, damage and edge refinement skip them",
            "Move the phone more slowly, especially when turning.",
        ))
    if n_kept < cfg.min_keep_frames:
        issues.append(Issue(
            "TOO_FEW_FRAMES", ERROR,
            f"only {n_kept} usable frames; at least {cfg.min_keep_frames} are needed",
            "Record a longer, slower capture covering every wall.",
        ))
    if kept_with_problems:
        issues.append(Issue(
            "MANY_FRAMES_DROPPED", WARNING,
            f"{kept_with_problems} frames with quality problems were kept to preserve coverage",
            "Move more slowly and keep the room well lit.",
        ))
    if meta.duration_s is not None and meta.duration_s < cfg.min_duration_s:
        issues.append(Issue(
            "SHORT_CAPTURE", WARNING,
            f"capture is only {meta.duration_s:.0f} s long",
            "Spend at least 30 s per room, walking the full perimeter.",
        ))

    fast = sum(1 for f in frames if "fast_motion" in f.reasons)
    if n_in and fast / n_in > cfg.fast_motion_warn_frac:
        issues.append(Issue(
            "FAST_MOTION", WARNING,
            f"{fast} of {n_in} keyframes were captured while moving or turning too fast",
            "Walk slowly and turn gradually, about one step per second.",
        ))
    if median_brightness < cfg.low_light_brightness:
        issues.append(Issue(
            "LOW_LIGHT", WARNING,
            f"scene is dark (median brightness {median_brightness:.0f}/255)",
            "Turn on all lights and open curtains before capturing.",
        ))
    if jumps_s:
        t0 = min((f.timestamp for f in frames), default=0.0)
        times = ", ".join(f"{t - t0:.1f}" for t in jumps_s[:5])
        issues.append(Issue(
            "TRACKING_JUMP", WARNING,
            f"{len(jumps_s)} camera tracking jump(s) at t = {times} s; nearby frames dropped",
            "Avoid covering the camera and pointing at blank walls up close.",
        ))

    if coverage.floor_seen is False:
        issues.append(Issue(
            "FLOOR_NOT_SEEN", ERROR,
            f"floor was not observed (only {coverage.floor_area_m2 or 0:.1f} m² of floor seen)",
            "Point the phone slightly down so the floor is in view while walking.",
        ))
    if coverage.ceiling_seen is False:
        issues.append(Issue(
            "CEILING_NOT_SEEN", WARNING,
            "ceiling was not observed, so ceiling heights will be reported as unavailable",
            "In each room, tilt the phone up toward the ceiling for about 5 seconds.",
        ))

    device = lookup_device(meta.device_model)
    if device is None:
        issues.append(Issue(
            "DEVICE_UNKNOWN", INFO,
            "iPhone model not recorded; device matrix check skipped",
            "Pass --device, e.g. --device \"iPhone 15 Pro\".",
        ))
    elif device.supported is False:
        issues.append(Issue(
            "DEVICE_UNSUPPORTED", WARNING,
            f"{device.name} is older than iPhone {MIN_SUPPORTED_GENERATION}; intervals are widened",
            "Use an iPhone 15 or newer.",
        ))
    return issues


def quality_score(frames: list[FrameQuality], issues: list[Issue]) -> float:
    """0-1 summary used by calibration (C12) to widen intervals on poor captures.

    Starts from the share of clean frames and applies a penalty per problem.
    Ceiling coverage is deliberately not penalised: it removes a measurement
    rather than degrading the others.
    """
    if not frames:
        return 0.0
    clean = sum(1 for f in frames if f.kept and not f.reasons and f.rgb_ok) / len(frames)
    score = 0.5 + 0.5 * clean
    penalties = {
        "LOW_LIGHT": 0.85,
        "TRACKING_JUMP": 0.85,
        "FAST_MOTION": 0.9,
        "DEVICE_UNSUPPORTED": 0.8,
        "DEVICE_UNKNOWN": 0.97,
        "SHORT_CAPTURE": 0.9,
        "MANY_BLURRY_FRAMES": 0.95,
        "TOO_FEW_FRAMES": 0.5,
        "FLOOR_NOT_SEEN": 0.5,
    }
    for issue in issues:
        score *= penalties.get(issue.code, 1.0)
    return round(float(score), 3)
