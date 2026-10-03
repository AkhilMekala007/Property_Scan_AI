"""Camera poses for unposed images: Depth Anything 3 (DA3-BASE, Apache-2.0).

Structure from motion fails on our photo and video captures (white walls, photos taken turning
on the spot, fast turns in video). DA3 predicts, for a set of images at once, a depth map per
image and every camera's pose in one shared frame, without needing feature tracks.

DA3's scale is arbitrary; the caller fixes it with the metric depth model (Depth Anything V2
metric-indoor). Long videos are processed in overlapping chunks joined through shared frames.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass

import numpy as np


@dataclass
class MultiViewResult:
    depth: np.ndarray  # (N, h, w) DA3 depth, arbitrary scale
    conf: np.ndarray  # (N, h, w)
    T_world_cam: np.ndarray  # (N, 4, 4) camera -> shared frame, OpenCV camera axes
    K: np.ndarray  # (N, 3, 3) at the depth map resolution


class MultiViewModel:
    def __init__(self, threads: int = 8):
        import torch

        from scan.models import require_weights

        # DA3's api module imports its export helpers (moviepy, trimesh, gsplat ...) at import
        # time; we never export, so a stub keeps those optional dependencies out.
        if "depth_anything_3.utils.export" not in sys.modules:
            stub = types.ModuleType("depth_anything_3.utils.export")
            stub.export = lambda *a, **k: None
            sys.modules["depth_anything_3.utils.export"] = stub
        from depth_anything_3.api import DepthAnything3

        torch.set_num_threads(threads)
        self._cls = DepthAnything3
        self.model = DepthAnything3.from_pretrained(str(require_weights("da3-base"))).eval()
        self._metric = None

    def metric_scale(self, images: list, result: MultiViewResult, n: int = 4, process_res: int = 336) -> list[float]:
        """Metres per DA3-BASE depth unit, per sampled image.

        DA3METRIC-LARGE predicts depth that becomes metric with the focal length
        (metres = focal_px * output / 300), so unlike a focal-blind metric model it adapts to the lens.
        It is slow on CPU, so only ``n`` spread-out images are used.
        """
        import cv2

        from scan.models import require_weights

        if self._metric is None:
            self._metric = self._cls.from_pretrained(str(require_weights("da3-metric-large"))).eval()
        picks = np.unique(np.linspace(0, len(images) - 1, min(n, len(images))).round().astype(int))
        out = []
        for k in picks:
            img = images[k]
            pred = self._metric.inference([str(img) if not isinstance(img, np.ndarray) else img],
                                          process_res=process_res)
            raw = pred.depth[0].astype(np.float32)
            base = cv2.resize(result.depth[k], raw.shape[::-1], interpolation=cv2.INTER_AREA)
            focal = result.K[k][0, 0] * raw.shape[1] / result.depth.shape[2]  # base focal at this resolution
            metric = focal * raw / 300.0
            ok = (base > 1e-3) & np.isfinite(metric)
            out.append(float(np.median(metric[ok] / base[ok])))
        return out

    def infer(self, images: list, process_res: int = 504) -> MultiViewResult:
        """images: file paths or RGB uint8 arrays."""
        pred = self.model.inference([str(i) if not isinstance(i, np.ndarray) else i for i in images],
                                    process_res=process_res)
        n = len(images)
        T = np.tile(np.eye(4), (n, 1, 1))
        for k in range(n):
            w2c = np.eye(4)
            w2c[:3, :4] = pred.extrinsics[k][:3, :4]
            T[k] = np.linalg.inv(w2c)
        return MultiViewResult(pred.depth.astype(np.float32), pred.conf.astype(np.float32), T,
                               pred.intrinsics.astype(np.float64))


def _sim3(A: np.ndarray, B: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    """s, R, t minimising |s R B + t - A| (Umeyama)."""
    ca, cb = A.mean(axis=0), B.mean(axis=0)
    A0, B0 = A - ca, B - cb
    U, S, Vt = np.linalg.svd(B0.T @ A0 / len(A))
    D = np.diag([1, 1, np.sign(np.linalg.det(Vt.T @ U.T))])
    R = Vt.T @ D @ U.T
    s = float(np.trace(np.diag(S) @ D) / (B0 ** 2).sum() * len(A))
    return s, R, ca - s * R @ cb


def join_chunks(chunks: list[tuple[list[int], MultiViewResult]]) -> tuple[dict[int, np.ndarray], dict[int, float],
                                                                           list[str]]:
    """Chain overlapping chunks into the first chunk's frame.

    Each chunk is (frame ids, result). Consecutive chunks share frames; the similarity transform
    between chunks comes from the shared cameras' centres and viewing directions. Returns per
    frame: pose in the joint frame, and the scale factor applied to that frame's chunk depth.
    """
    poses: dict[int, np.ndarray] = {}
    scales: dict[int, float] = {}
    depth_of: dict[int, np.ndarray] = {}
    notes = []
    s_prev, G_prev = 1.0, np.eye(4)
    prev_ids, prev_T = None, None
    for c, (ids, res) in enumerate(chunks):
        if prev_ids is None:
            s, G = 1.0, np.eye(4)
        else:
            shared = [i for i in ids if i in prev_ids]
            if len(shared) < 2:
                notes.append(f"chunk {c}: fewer than 2 shared frames; joined by the last pose only")
                shared = shared or []
            if len(shared) >= 2:
                Ta = [poses[i] for i in shared]
                Tb = [res.T_world_cam[ids.index(i)] for i in shared]
                # rotation from the shared cameras' orientations (robust when centres barely move)
                U, _, Vt = np.linalg.svd(sum(x[:3, :3] @ y[:3, :3].T for x, y in zip(Ta, Tb)))
                R = U @ np.diag([1, 1, np.sign(np.linalg.det(U @ Vt))]) @ Vt
                a = np.array([x[:3, 3] for x in Ta])
                b = np.array([y[:3, 3] for y in Tb]) @ R.T
                # scale from the depth maps of the shared images (same image, two predictions): far more
                # stable than from camera centres, which move only a little across the overlap
                ratios = []
                for i in shared:
                    dp, dc = depth_of[i], res.depth[ids.index(i)]
                    ok = (dp > 1e-6) & (dc > 1e-6)
                    if ok.sum() > 100:
                        ratios.append(float(np.median(dp[ok] / dc[ok])))
                if ratios:
                    s = float(np.median(ratios))
                else:
                    a0, b0 = a - a.mean(axis=0), b - b.mean(axis=0)
                    s = float((a0 * b0).sum() / max((b0 ** 2).sum(), 1e-12))
                t = a.mean(axis=0) - s * b.mean(axis=0)
            else:
                i = shared[0] if shared else prev_ids[-1]
                j = ids.index(i) if i in ids else 0
                s = scales[i] if i in scales else s_prev
                R = poses[i][:3, :3] @ res.T_world_cam[j][:3, :3].T
                t = poses[i][:3, 3] - s * R @ res.T_world_cam[j][:3, 3]
            G = np.eye(4)
            G[:3, :3], G[:3, 3] = R, t
        for k, i in enumerate(ids):
            if i in poses:
                continue
            T = res.T_world_cam[k].copy()
            T[:3, 3] *= s
            poses[i] = G @ T
            scales[i] = s
            depth_of[i] = res.depth[k] * s  # in joint units
        prev_ids, s_prev = ids, s
    return poses, scales, notes
