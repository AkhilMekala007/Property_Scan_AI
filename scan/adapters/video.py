"""C2 video adapter: a plain walkthrough video -> FrameSet with metric depth and poses.

1. Keyframes: one pass over the video; the sharpest frame in each time window.
2. Poses: COLMAP structure from motion (SIFT, sequential matching, incremental mapping).
3. Depth: Depth Anything V2 metric-indoor per keyframe.
4. Scale: SfM has no scale; the depth model's metres give it (robust median over frames),
   and SfM's consistency corrects each frame's depth.
5. Gravity: "up" from the camera's image-up direction, refined with horizontal surfaces.

Everything is cached under outputs/cache/<capture>/video/, so reruns are fast.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, replace
from pathlib import Path

import cv2
import numpy as np

from scan.adapters.base import AdapterConfig
from scan.core.geometry import organised_points_normals
from scan.core.types import CaptureMeta, Frame, FrameSet, Intrinsics, Trajectory
from scan.ingest.detect import DetectedCapture
from scan.io.video import probe, read_rgb

DEPTH_SIZE = (256, 144)  # depth grid for 16:9 video, like LiDAR's 256 x 192


@dataclass(frozen=True)
class VideoConfig:
    target_keyframes: int = 500  # ~3 per second: white walls need heavy overlap between neighbours
    colmap_max_size: int = 1280
    sequential_overlap: int = 12
    sift_peak_threshold: float = 0.004  # COLMAP default 0.0067; lower finds more points on smooth walls
    abs_pose_min_inliers: int = 15  # default 30
    init_min_inliers: int = 50  # default 100
    max_depth_frames: int = 200  # depth is the slow step; SfM uses every keyframe, depth a spread subset
    depth_rel_sigma: float = 0.06  # one-sigma relative depth noise of the estimated depth
    max_frame_scale_fix: float = 0.3  # per-frame depth correction is clipped to +-30 %


# ---------- keyframes ----------

def select_keyframes(video_path: Path, target: int, out_dir: Path) -> list[tuple[int, float]]:
    """Sharpest frame per equal time window; writes them as JPEGs. Returns [(frame index, t)]."""
    info = probe(video_path)
    n = info.n_frames
    window = max(1, n // target)
    cap = cv2.VideoCapture(str(video_path))
    best: dict[int, tuple[float, int, np.ndarray]] = {}
    i = 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if i % 3 == 0:  # score every 3rd frame (10 fps) to keep the pass fast
            ok, frame = cap.retrieve()
            if ok:
                small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (480, 270))
                sharp = float(cv2.Laplacian(small, cv2.CV_64F).var())
                w = i // window
                if w not in best or sharp > best[w][0]:
                    best[w] = (sharp, i, frame)
        i += 1
    cap.release()
    out_dir.mkdir(parents=True, exist_ok=True)
    keys = []
    for w in sorted(best):
        _, idx, frame = best[w]
        cv2.imwrite(str(out_dir / f"{idx:06d}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
        keys.append((idx, idx / info.fps))
    return keys


# ---------- structure from motion ----------

def _rigid_matrix(image) -> np.ndarray:
    cfw = image.cam_from_world() if callable(image.cam_from_world) else image.cam_from_world
    M = np.eye(4)
    M[:3, :4] = cfw.matrix()
    return M


def run_sfm(image_dir: Path, work: Path, cfg: VideoConfig):
    import pycolmap

    small = work / "images"
    small.mkdir(parents=True, exist_ok=True)
    for p in sorted(image_dir.glob("*.jpg")):
        dst = small / p.name
        if not dst.exists():
            img = cv2.imread(str(p))
            s = cfg.colmap_max_size / max(img.shape[:2])
            cv2.imwrite(str(dst), cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA),
                        [cv2.IMWRITE_JPEG_QUALITY, 95])
    db = work / "database.db"
    if db.exists():
        db.unlink()
    reader = pycolmap.ImageReaderOptions()
    reader.camera_model = "SIMPLE_RADIAL"
    extraction = pycolmap.FeatureExtractionOptions()
    extraction.max_image_size = cfg.colmap_max_size
    extraction.use_gpu = False
    extraction.sift.peak_threshold = cfg.sift_peak_threshold
    pycolmap.extract_features(db, small, camera_mode=pycolmap.CameraMode.SINGLE, reader_options=reader,
                              extraction_options=extraction, device=pycolmap.Device.cpu)
    pairing = pycolmap.SequentialPairingOptions()
    pairing.overlap = cfg.sequential_overlap
    pairing.quadratic_overlap = True
    matching = pycolmap.FeatureMatchingOptions()
    matching.use_gpu = False
    pycolmap.match_sequential(db, matching_options=matching, pairing_options=pairing, device=pycolmap.Device.cpu)
    sparse = work / "sparse"
    if sparse.exists():
        shutil.rmtree(sparse)
    sparse.mkdir()
    options = pycolmap.IncrementalPipelineOptions()
    options.min_num_matches = 10
    options.mapper.abs_pose_min_num_inliers = cfg.abs_pose_min_inliers
    options.mapper.init_min_num_inliers = cfg.init_min_inliers
    recs = pycolmap.incremental_mapping(db, small, sparse, options=options)
    if not recs:
        raise RuntimeError("structure from motion failed: no images could be registered")
    rec = max(recs.values(), key=lambda r: r.num_reg_images())
    rec.write(str(sparse))
    return rec


# ---------- depth ----------

class DepthModel:
    def __init__(self, threads: int = 4):
        import torch
        from transformers import AutoImageProcessor, DepthAnythingForDepthEstimation

        from scan.models import require_weights

        torch.set_num_threads(threads)
        path = require_weights("depth-anything-v2-metric-indoor-small")
        self._torch = torch
        self.processor = AutoImageProcessor.from_pretrained(path, local_files_only=True)
        self.model = DepthAnythingForDepthEstimation.from_pretrained(path, local_files_only=True).eval()

    def predict(self, rgb: np.ndarray, size: tuple[int, int]) -> np.ndarray:
        inputs = self.processor(images=rgb, return_tensors="pt")
        with self._torch.inference_mode():
            depth = self.model(**inputs).predicted_depth[0].numpy()
        return cv2.resize(depth, size, interpolation=cv2.INTER_AREA).astype(np.float32)


# ---------- scale and gravity ----------

def frame_scales(rec, depths: dict[str, np.ndarray], colmap_size: tuple[int, int]) -> dict[str, float]:
    """Per image: median(predicted metric depth / SfM depth) at the image's triangulated points."""
    out = {}
    w0, h0 = colmap_size
    for img in rec.images.values():
        if img.name not in depths:
            continue
        T = _rigid_matrix(img)
        d = depths[img.name]
        ratios = []
        for p2 in img.points2D:
            if not p2.has_point3D():
                continue
            X = rec.points3D[p2.point3D_id].xyz
            z = (T[:3, :3] @ X + T[:3, 3])[2]
            if z <= 0:
                continue
            u = int(p2.xy[0] / w0 * d.shape[1])
            v = int(p2.xy[1] / h0 * d.shape[0])
            if 0 <= u < d.shape[1] and 0 <= v < d.shape[0]:
                ratios.append(d[v, u] / z)
        if len(ratios) >= 20:
            out[img.name] = float(np.median(ratios))
    return out


def gravity_rotation(frames: list[Frame]) -> np.ndarray:
    """Rotation taking the estimated 'up' to world +y.

    Start from the mean image-up direction of the cameras (people hold phones roughly upright),
    then refine with the mean normal of horizontal surfaces (floors and ceilings).
    """
    ups = np.array([-f.T_world_cam[:3, 1] for f in frames])  # camera -y is image up
    up = ups.mean(axis=0)
    up /= np.linalg.norm(up)
    normals = []
    for f in frames[:: max(1, len(frames) // 40)]:
        _, N = organised_points_normals(f.depth(), f.depth_intrinsics, f.T_world_cam, stride=4)
        N = N.reshape(-1, 3)
        N = N[np.all(np.isfinite(N), axis=1)]
        cos = N @ up
        sel = np.abs(cos) > np.cos(np.radians(25))
        normals.append(N[sel] * np.sign(cos[sel])[:, None])
    if normals and sum(len(n) for n in normals) > 500:
        refined = np.concatenate(normals).mean(axis=0)
        up = refined / np.linalg.norm(refined)
    target = np.array([0.0, 1.0, 0.0])
    v = np.cross(up, target)
    s, c = np.linalg.norm(v), float(up @ target)
    if s < 1e-9:
        return np.eye(3) if c > 0 else np.diag([1.0, -1.0, -1.0])
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * ((1 - c) / s ** 2)


def _confidence(depth: np.ndarray) -> np.ndarray:
    """No sensor confidence: mark depth discontinuities (where estimates blur across edges) as low."""
    gx = cv2.Sobel(depth, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(depth, cv2.CV_32F, 0, 1, ksize=3)
    rel = np.hypot(gx, gy) / np.maximum(depth, 0.1)
    conf = np.full(depth.shape, 2, np.uint8)
    conf[rel > 0.15] = 1
    conf[rel > 0.4] = 0
    return conf


# ---------- adapter ----------

class VideoAdapter:
    def __init__(self, cfg: VideoConfig | None = None):
        self.cfg = cfg or VideoConfig()

    def load(self, detected: DetectedCapture, cache_root: Path, config: AdapterConfig) -> FrameSet:
        cfg = self.cfg
        video = detected.video_path
        info = probe(video)
        meta = CaptureMeta(detected.capture_id, detected.tier, detected.source_format, detected.root,
                           device_model=config.device_model, n_frames_raw=info.n_frames,
                           duration_s=info.n_frames / info.fps if info.fps else None)
        if meta.device_model is None:
            meta.warn("video files do not reliably record the iPhone model; pass --device")
        cache = cache_root / meta.capture_id / "video"
        rgb_dir = cache / "rgb"
        manifest = cache / "keyframes.json"
        sig = {"video": video.name, "size": video.stat().st_size, "target": cfg.target_keyframes}
        if manifest.exists() and json.loads(manifest.read_text())["sig"] == sig:
            keys = [tuple(k) for k in json.loads(manifest.read_text())["keys"]]
        else:
            if cache.exists():
                shutil.rmtree(cache)
            keys = select_keyframes(video, cfg.target_keyframes, rgb_dir)
            manifest.write_text(json.dumps({"sig": sig, "keys": keys}))

        sfm_dir = cache / "colmap"
        if (sfm_dir / "sparse" / "images.bin").exists():
            import pycolmap

            rec = pycolmap.Reconstruction(str(sfm_dir / "sparse"))
        else:
            rec = run_sfm(rgb_dir, sfm_dir, cfg)
        registered = {img.name: img for img in rec.images.values()}
        meta.warn(f"structure from motion registered {len(registered)} of {len(keys)} keyframes") \
            if len(registered) < 0.9 * len(keys) else None

        depth_dir = cache / "depth"
        depth_dir.mkdir(exist_ok=True)
        model = None
        depths = {}
        names = sorted(registered)
        if len(names) > cfg.max_depth_frames:
            picks = np.unique(np.linspace(0, len(names) - 1, cfg.max_depth_frames).round().astype(int))
            names = [names[k] for k in picks]
        registered = {n: registered[n] for n in names}
        for name in registered:
            p = depth_dir / (Path(name).stem + ".npy")
            if not p.exists():
                model = model or DepthModel()
                np.save(p, model.predict(read_rgb(rgb_dir / name), DEPTH_SIZE).astype(np.float16))
            depths[name] = np.load(p).astype(np.float32)

        cam = next(iter(rec.cameras.values()))
        colmap_w, colmap_h = cam.width, cam.height
        scales = frame_scales(rec, depths, (colmap_w, colmap_h))
        if len(scales) < 5:
            raise RuntimeError("too few frames with triangulated points to recover metric scale")
        scale = float(np.median(list(scales.values())))  # metres per SfM unit
        spread = float(np.median(np.abs(np.array(list(scales.values())) / scale - 1)))
        meta.warn(f"metric scale from depth model: {scale:.4f} m per SfM unit, frame-to-frame spread {spread:.1%}")

        f_px = cam.params[0] * info.width / colmap_w
        intr = Intrinsics(f_px, f_px, cam.params[1] * info.width / colmap_w,
                          cam.params[2] * info.height / colmap_h, info.width, info.height)

        frames = []
        idx_time = {f"{i:06d}.jpg": (i, t) for i, t in keys}
        for name, img in sorted(registered.items()):
            i, t = idx_time[name]
            T_cw = _rigid_matrix(img)
            T_wc = np.linalg.inv(T_cw)
            T_wc[:3, 3] *= scale
            fix = float(np.clip(scale / scales[name], 1 - cfg.max_frame_scale_fix, 1 + cfg.max_frame_scale_fix)) \
                if name in scales else 1.0
            depth = depths[name] * fix
            conf = _confidence(depth)
            sigma = (cfg.depth_rel_sigma * depth).astype(np.float32)
            frames.append(Frame(
                index=i, timestamp=t, intrinsics=intr, T_world_cam=T_wc, depth_size=DEPTH_SIZE,
                _rgb=lambda p=rgb_dir / name: read_rgb(p),
                _depth=lambda d=depth: d, _confidence=lambda c=conf: c, _depth_sigma=lambda s=sigma: s))

        R = gravity_rotation(frames)
        G = np.eye(4)
        G[:3, :3] = R
        frames = [replace(f, T_world_cam=G @ f.T_world_cam) for f in frames]
        traj = Trajectory(np.array([f.timestamp for f in frames]),
                          np.array([f.T_world_cam[:3, 3] for f in frames]),
                          np.stack([f.T_world_cam[:3, :3] for f in frames]))
        return FrameSet(meta=meta, frames=frames, cache_dir=cache_root / meta.capture_id, trajectory=traj)
