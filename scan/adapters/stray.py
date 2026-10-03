"""C2 LiDAR adapter for Stray Scanner exports.

Export layout (one capture)::

    rgb.mp4              1920x1440 video, one frame per odometry row
    depth/000000.png     uint16 millimetres, 256x192
    confidence/000000.png  uint8 ARKit confidence 0/1/2
    odometry.csv         timestamp, frame, x, y, z, qx, qy, qz, qw[, fx, fy, cx, cy, ...]
    camera_matrix.csv    3x3 intrinsics at RGB resolution
    imu.csv              accelerometer + gyro (unused for now)

The odometry pose maps OpenCV camera points (+x right, +y down, +z forward)
into the gravity-aligned ARKit world (+y up), which is exactly the project
convention, so poses are used without axis flips. This was verified on the
sample captures: back-projected floors land ~1.45 m below the handheld phone.
"""

from __future__ import annotations

import csv
from pathlib import Path

import cv2
import numpy as np

from scan.adapters.base import AdapterConfig, KeyframeConfig, LidarNoiseModel
from scan.core.geometry import pose_matrix, quat_to_rotmat, rotation_angle_deg
from scan.core.types import CaptureMeta, Frame, FrameSet, Intrinsics, Trajectory
from scan.ingest.detect import DetectedCapture
from scan.io.video import extract_frames, probe, read_rgb

DEPTH_SANITY_SAMPLES = 12


class StrayScannerAdapter:
    def load(self, detected: DetectedCapture, cache_root: Path, config: AdapterConfig) -> FrameSet:
        root = detected.root
        meta = CaptureMeta(
            capture_id=detected.capture_id,
            tier=detected.tier,
            source_format=detected.source_format,
            source_dir=root,
            device_model=config.device_model,
        )
        if meta.device_model is None:
            meta.warn(
                "Stray Scanner exports do not record the iPhone model; "
                "pass --device to state it (device matrix check skipped)"
            )

        odo = read_odometry(root / "odometry.csv")
        K = np.loadtxt(root / "camera_matrix.csv", delimiter=",")
        video = probe(root / "rgb.mp4")
        depth_files = sorted((root / "depth").glob("*.png"))
        conf_dir = root / "confidence"
        conf_files = sorted(conf_dir.glob("*.png")) if conf_dir.is_dir() else []

        n = _usable_frame_count(meta, len(odo["timestamp"]), len(depth_files), len(conf_files), video.n_frames)
        meta.n_frames_raw = n
        _validate_odometry(meta, odo, n)
        ts = odo["timestamp"][:n]
        meta.duration_s = float(ts[-1] - ts[0]) if n > 1 else 0.0

        depth_size = _check_depth(meta, depth_files[:n])
        has_conf = len(conf_files) >= n
        if not has_conf:
            meta.warn("confidence maps missing; depth uncertainty assumes high confidence everywhere")

        rotations = [quat_to_rotmat(*q) for q in odo["quat"][:n]]
        positions = odo["pos"][:n]
        keys = select_keyframes(positions, rotations, config.keyframes)

        rgb_paths = extract_frames(
            root / "rgb.mp4",
            keys,
            cache_root / meta.capture_id / "rgb",
            jpeg_quality=config.jpeg_quality,
        )

        frames = []
        for i in keys:
            intr = _frame_intrinsics(odo, i, K, video.width, video.height)
            depth_path = root / "depth" / f"{i:06d}.png"
            conf_path = conf_dir / f"{i:06d}.png" if has_conf else None
            frames.append(
                Frame(
                    index=i,
                    timestamp=float(ts[i]),
                    intrinsics=intr,
                    T_world_cam=pose_matrix(positions[i], rotations[i]),
                    depth_size=depth_size,
                    _rgb=lambda p=rgb_paths[i]: read_rgb(p),
                    _depth=lambda p=depth_path: load_depth_m(p),
                    _confidence=(lambda p=conf_path: load_confidence(p)) if conf_path else None,
                    _depth_sigma=lambda d=depth_path, c=conf_path, m=config.lidar_noise: lidar_sigma(
                        load_depth_m(d), load_confidence(c) if c else None, m
                    ),
                )
            )
        trajectory = Trajectory(
            timestamps=np.asarray(ts, dtype=np.float64),
            positions=np.asarray(positions, dtype=np.float64),
            rotations=np.stack(rotations),
        )
        return FrameSet(
            meta=meta, frames=frames, cache_dir=cache_root / meta.capture_id, trajectory=trajectory
        )


def read_odometry(path: Path) -> dict[str, np.ndarray]:
    with path.open(newline="") as fh:
        reader = csv.reader(fh)
        header = [h.strip() for h in next(reader)]
        rows = [r for r in reader if r and any(c.strip() for c in r)]
    col = {name: i for i, name in enumerate(header)}
    missing = [c for c in ("timestamp", "frame", "x", "y", "z", "qx", "qy", "qz", "qw") if c not in col]
    if missing:
        raise ValueError(f"{path.name} is missing columns: {', '.join(missing)}")

    def numeric(name: str) -> np.ndarray:
        return np.array([float(r[col[name]]) for r in rows])

    out = {
        "timestamp": numeric("timestamp"),
        "frame": numeric("frame").astype(int),
        "pos": np.stack([numeric("x"), numeric("y"), numeric("z")], axis=1),
        "quat": np.stack([numeric("qx"), numeric("qy"), numeric("qz"), numeric("qw")], axis=1),
    }
    if all(c in col for c in ("fx", "fy", "cx", "cy")):
        out["intrinsics"] = np.stack([numeric(c) for c in ("fx", "fy", "cx", "cy")], axis=1)
    return out


def select_keyframes(
    positions: np.ndarray, rotations: list[np.ndarray], cfg: KeyframeConfig
) -> list[int]:
    """Deterministic keyframes: a new one whenever the camera moved or turned enough.

    If the motion rule yields more than ``max_keyframes``, the selection is thinned
    evenly so coverage of the whole walk is kept.
    """
    if len(positions) == 0:
        return []
    keys = [0]
    for i in range(1, len(positions)):
        last = keys[-1]
        moved = np.linalg.norm(positions[i] - positions[last]) >= cfg.min_translation_m
        turned = rotation_angle_deg(rotations[last], rotations[i]) >= cfg.min_rotation_deg
        if moved or turned:
            keys.append(i)
    if len(keys) > cfg.max_keyframes:
        picks = np.linspace(0, len(keys) - 1, cfg.max_keyframes).round().astype(int)
        keys = [keys[j] for j in np.unique(picks)]
    return keys


def load_depth_m(path: Path) -> np.ndarray:
    raw = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if raw is None:
        raise OSError(f"cannot read depth map {path}")
    depth = raw.astype(np.float32) / 1000.0
    depth[raw == 0] = np.nan
    return depth


def load_confidence(path: Path) -> np.ndarray:
    conf = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if conf is None:
        raise OSError(f"cannot read confidence map {path}")
    return conf


def lidar_sigma(depth: np.ndarray, confidence: np.ndarray | None, model: LidarNoiseModel) -> np.ndarray:
    sigma = (model.base_m + model.quad_per_m * depth**2).astype(np.float32)
    if confidence is not None:
        sigma[confidence == 1] *= model.medium_conf_factor
        sigma[confidence == 0] = np.nan
    sigma[~np.isfinite(depth)] = np.nan
    return sigma


def _usable_frame_count(meta: CaptureMeta, n_odo: int, n_depth: int, n_conf: int, n_video: int) -> int:
    counts = {"odometry rows": n_odo, "depth maps": n_depth, "video frames": n_video}
    if n_conf:
        counts["confidence maps"] = n_conf
    n = min(c for c in counts.values() if c > 0) if any(counts.values()) else 0
    if n == 0:
        raise ValueError(f"capture {meta.capture_id} has no usable frames ({counts})")
    # The container's frame count can be off by one; only flag real mismatches.
    if max(counts.values()) - n > 1:
        detail = ", ".join(f"{k}={v}" for k, v in counts.items())
        meta.warn(f"frame counts differ ({detail}); using the first {n} frames")
    return n


def _validate_odometry(meta: CaptureMeta, odo: dict[str, np.ndarray], n: int) -> None:
    ts = odo["timestamp"][:n]
    non_increasing = int(np.sum(np.diff(ts) <= 0))
    if non_increasing:
        meta.warn(f"{non_increasing} odometry timestamps are not increasing")
    if not np.array_equal(odo["frame"][:n], np.arange(n)):
        meta.warn("odometry 'frame' column is not 0..N-1; rows are matched to files by order")
    norms = np.linalg.norm(odo["quat"][:n], axis=1)
    bad = int(np.sum(np.abs(norms - 1.0) > 1e-3))
    if bad:
        meta.warn(f"{bad} pose quaternions are not unit length (normalised on load)")


def _check_depth(meta: CaptureMeta, depth_files: list[Path]) -> tuple[int, int]:
    picks = np.linspace(0, len(depth_files) - 1, min(DEPTH_SANITY_SAMPLES, len(depth_files)))
    sizes, valid_fracs = set(), []
    for j in np.unique(picks.round().astype(int)):
        d = load_depth_m(depth_files[j])
        sizes.add((d.shape[1], d.shape[0]))
        valid_fracs.append(float(np.isfinite(d).mean()))
    if len(sizes) != 1:
        raise ValueError(f"depth maps have inconsistent sizes: {sorted(sizes)}")
    if np.median(valid_fracs) < 0.5:
        meta.warn(f"depth is mostly empty (median {np.median(valid_fracs):.0%} valid pixels)")
    return sizes.pop()


def _frame_intrinsics(odo: dict[str, np.ndarray], i: int, K: np.ndarray, width: int, height: int) -> Intrinsics:
    if "intrinsics" in odo and np.all(np.isfinite(odo["intrinsics"][i])):
        fx, fy, cx, cy = odo["intrinsics"][i]
    else:
        fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
    return Intrinsics(float(fx), float(fy), float(cx), float(cy), width, height)
