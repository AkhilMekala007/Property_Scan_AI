from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

RGB_W, RGB_H = 64, 48
DEPTH_W, DEPTH_H = 16, 12
FX = FY = 50.0
CX, CY = 31.5, 23.5
ODOMETRY_HEADER = (
    "timestamp, frame, x, y, z, qx, qy, qz, qw, fx, fy, cx, cy, "
    "distortion_center_x, distortion_center_y"
)


def write_stray_capture(
    root: Path,
    n_frames: int = 10,
    step_m: float = 0.06,
    depth_mm: int = 2000,
) -> Path:
    """A tiny but structurally faithful Stray Scanner export.

    The camera slides along +x by ``step_m`` per frame with identity rotation and
    sees a flat wall ``depth_mm`` away; the top-left depth pixel is a dropout (0).
    """
    root.mkdir(parents=True)
    (root / "depth").mkdir()
    (root / "confidence").mkdir()

    writer = cv2.VideoWriter(
        str(root / "rgb.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), 30.0, (RGB_W, RGB_H)
    )
    assert writer.isOpened(), "OpenCV cannot write mp4v test videos"
    for i in range(n_frames):
        writer.write(np.full((RGB_H, RGB_W, 3), 20 * i % 255, np.uint8))
    writer.release()

    rows = [ODOMETRY_HEADER]
    for i in range(n_frames):
        depth = np.full((DEPTH_H, DEPTH_W), depth_mm, np.uint16)
        depth[0, 0] = 0
        cv2.imwrite(str(root / "depth" / f"{i:06d}.png"), depth)
        conf = np.full((DEPTH_H, DEPTH_W), 2, np.uint8)
        conf[1, :] = 1
        conf[2, :] = 0
        cv2.imwrite(str(root / "confidence" / f"{i:06d}.png"), conf)
        rows.append(
            f"{100 + i / 30:.6f}, {i:06d}, {i * step_m:.6f}, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, "
            f"{FX}, {FY}, {CX}, {CY}, , "
        )
    (root / "odometry.csv").write_text("\n".join(rows) + "\n")
    (root / "camera_matrix.csv").write_text(f"{FX}, 0.0, {CX}\n0.0, {FY}, {CY}\n0.0, 0.0, 1.0")
    (root / "imu.csv").write_text("timestamp, a_x, a_y, a_z, alpha_x, alpha_y, alpha_z\n")
    return root


@pytest.fixture
def stray_capture(tmp_path: Path) -> Path:
    return write_stray_capture(tmp_path / "wrapper" / "abc123")


@pytest.fixture
def cache_root(tmp_path: Path) -> Path:
    return tmp_path / "cache"
