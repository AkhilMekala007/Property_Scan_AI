"""Video and photo tiers: build fragments, run the per-room pipeline on each, stitch by doors.

A fragment is a set of frames that share one reconstruction: a photo-tier room folder, or one
connected piece of the video's structure from motion (handheld video of white walls breaks
into pieces at fast turns and doorways). Each fragment gets metric scale from the depth model
and gravity from its horizontal surfaces, then runs the same C3-C8 pipeline as LiDAR. The
fragments are placed relative to each other by matching doors seen from both sides.
"""

from __future__ import annotations

import copy
import json
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from scan.adapters.video import (
    DEPTH_SIZE,
    DepthModel,
    VideoConfig,
    _confidence,
    _rigid_matrix,
    frame_scales,
    gravity_rotation,
    select_keyframes,
)
from scan.core.types import CaptureMeta, Frame, FrameSet, Intrinsics, Tier, Trajectory
from scan.io.photos import photo_info, read_photo
from scan.io.video import probe, read_rgb

PHOTO_MAX_SIDE = 1008
PHOTO_DA3_RES = 504  # DA3-BASE input size for photos (392 tested: metric scale ~15 % low, walls 1/7 in gate)
PHOTO_METRIC_N = 4  # photos per room for the DA3METRIC scale (2 tested: scale unstable)
MIN_VIDEO_FRAGMENT = 6


@dataclass
class Fragment:
    name: str
    frameset: FrameSet
    note: str = ""
    layout_xz: dict[int, np.ndarray] | None = None  # frame index -> camera xz in a common frame (layout only)


# ---------- shared ----------

def _frames_from_reconstruction(rec, image_dir: Path, depth_dir: Path, depths: dict[str, np.ndarray],
                                full_size: tuple[int, int], index_of: dict[str, int], time_of: dict[str, float],
                                cfg: VideoConfig, notes: list[str]) -> list[Frame]:
    cam = next(iter(rec.cameras.values()))
    W, H = full_size
    sx = W / cam.width
    f_px = cam.params[0] * sx
    intr = Intrinsics(f_px, f_px, cam.params[1] * sx, cam.params[2] * sx, W, H)
    scales = frame_scales(rec, depths, (cam.width, cam.height))
    if len(scales) >= 2:
        scale = float(np.median(list(scales.values())))
        spread = float(np.median(np.abs(np.array(list(scales.values())) / scale - 1)))
        notes.append(f"scale {scale:.4f} m/unit from {len(scales)} frames (spread {spread:.0%})")
    else:
        scale = 1.0
        notes.append("scale: too few triangulated points, using depth-model scale only")
    depth_size = next(iter(depths.values())).shape[::-1]
    frames = []
    for img in rec.images.values():
        if img.name not in depths:
            continue
        T_wc = np.linalg.inv(_rigid_matrix(img))
        T_wc[:3, 3] *= scale
        fix = float(np.clip(scale / scales[img.name], 1 - cfg.max_frame_scale_fix, 1 + cfg.max_frame_scale_fix)) \
            if img.name in scales else 1.0
        depth = depths[img.name] * fix
        frames.append(Frame(
            index=index_of[img.name], timestamp=time_of[img.name], intrinsics=intr, T_world_cam=T_wc,
            depth_size=tuple(depth_size), _rgb=lambda p=image_dir / img.name: read_rgb(p),
            _depth=lambda d=depth: d, _confidence=lambda c=_confidence(depth): c,
            _depth_sigma=lambda s=(cfg.depth_rel_sigma * depth).astype(np.float32): s))
    return _gravity_align(frames)


def _gravity_align(frames: list[Frame]) -> list[Frame]:
    if not frames:
        return frames
    G = np.eye(4)
    G[:3, :3] = gravity_rotation(frames)
    return [replace(f, T_world_cam=G @ f.T_world_cam) for f in sorted(frames, key=lambda f: f.index)]


def _frameset(meta: CaptureMeta, frames: list[Frame], cache: Path) -> FrameSet:
    traj = Trajectory(np.array([f.timestamp for f in frames]), np.array([f.T_world_cam[:3, 3] for f in frames]),
                      np.stack([f.T_world_cam[:3, :3] for f in frames]))
    return FrameSet(meta=meta, frames=frames, cache_dir=cache, trajectory=traj)


def _sfm(image_dir: Path, work: Path, exhaustive: bool, camera_params: str | None, cfg: VideoConfig,
         reuse_matches: bool = True):
    """COLMAP on a folder of images; returns ALL reconstructions, largest first."""
    import pycolmap

    work.mkdir(parents=True, exist_ok=True)
    db = work / "database.db"
    have_matches = db.exists() and reuse_matches
    if not have_matches:
        if db.exists():
            db.unlink()
        reader = pycolmap.ImageReaderOptions()
        reader.camera_model = "SIMPLE_RADIAL"
        if camera_params:
            reader.camera_params = camera_params
        ext = pycolmap.FeatureExtractionOptions()
        ext.use_gpu = False
        ext.max_image_size = cfg.colmap_max_size
        ext.sift.peak_threshold = cfg.sift_peak_threshold
        pycolmap.extract_features(db, image_dir, camera_mode=pycolmap.CameraMode.SINGLE, reader_options=reader,
                                  extraction_options=ext, device=pycolmap.Device.cpu)
        matching = pycolmap.FeatureMatchingOptions()
        matching.use_gpu = False
        if exhaustive:
            pycolmap.match_exhaustive(db, matching_options=matching, device=pycolmap.Device.cpu)
        else:
            pairing = pycolmap.SequentialPairingOptions()
            pairing.overlap = cfg.sequential_overlap
            pairing.quadratic_overlap = True
            pycolmap.match_sequential(db, matching_options=matching, pairing_options=pairing,
                                      device=pycolmap.Device.cpu)
    sparse = work / "sparse_all"
    if sparse.exists():
        shutil.rmtree(sparse)
    sparse.mkdir()
    options = pycolmap.IncrementalPipelineOptions()
    options.min_num_matches = 10
    options.min_model_size = 2 if exhaustive else MIN_VIDEO_FRAGMENT
    options.mapper.abs_pose_min_num_inliers = cfg.abs_pose_min_inliers
    options.mapper.init_min_num_inliers = 30 if exhaustive else cfg.init_min_inliers
    if camera_params:
        options.ba_refine_focal_length = False  # EXIF focal is reliable; few photos can't refine it
    recs = pycolmap.incremental_mapping(db, image_dir, sparse, options=options)
    return sorted(recs.values(), key=lambda r: -r.num_reg_images())


# ---------- video ----------

def video_fragments(video: Path, capture_id: str, cache_root: Path, device: str | None,
                    cfg: VideoConfig | None = None) -> tuple[CaptureMeta, list[Fragment]]:
    cfg = cfg or VideoConfig()
    info = probe(video)
    meta = CaptureMeta(capture_id, Tier.VIDEO, "video_file", video.parent, device_model=device,
                       n_frames_raw=info.n_frames, duration_s=info.n_frames / info.fps)
    cache = cache_root / capture_id / "video"
    rgb_dir = cache / "rgb"
    manifest = cache / "keyframes.json"
    sig = {"video": video.name, "size": video.stat().st_size, "target": cfg.target_keyframes}
    if manifest.exists() and json.loads(manifest.read_text())["sig"] == sig:
        keys = [tuple(k) for k in json.loads(manifest.read_text())["keys"]]
    else:
        keys = select_keyframes(video, cfg.target_keyframes, rgb_dir)
        manifest.write_text(json.dumps({"sig": sig, "keys": keys}))
    small = cache / "colmap" / "images"
    small.mkdir(parents=True, exist_ok=True)
    for i, _ in keys:
        dst = small / f"{i:06d}.jpg"
        if not dst.exists():
            img = cv2.imread(str(rgb_dir / f"{i:06d}.jpg"))
            s = cfg.colmap_max_size / max(img.shape[:2])
            cv2.imwrite(str(dst), cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA))
    recs = [r for r in _sfm(small, cache / "colmap", False, None, cfg) if r.num_reg_images() >= MIN_VIDEO_FRAGMENT]
    total = sum(r.num_reg_images() for r in recs)
    meta.warn(f"structure from motion: {len(recs)} pieces covering {total} of {len(keys)} keyframes")
    index_of = {f"{i:06d}.jpg": i for i, _ in keys}
    time_of = {f"{i:06d}.jpg": t for i, t in keys}
    depth_dir = cache / "depth"
    depth_dir.mkdir(exist_ok=True)
    model = None
    fragments = []
    budget = cfg.max_depth_frames
    for k, rec in enumerate(recs):
        names = sorted(img.name for img in rec.images.values())
        n_depth = max(6, int(round(budget * len(names) / max(total, 1))))
        if len(names) > n_depth:
            names = [names[j] for j in np.unique(np.linspace(0, len(names) - 1, n_depth).round().astype(int))]
        depths = {}
        for name in names:
            p = depth_dir / (Path(name).stem + ".npy")
            if not p.exists():
                model = model or DepthModel()
                np.save(p, model.predict(read_rgb(rgb_dir / name), DEPTH_SIZE).astype(np.float16))
            depths[name] = np.load(p).astype(np.float32)
        notes: list[str] = []
        frames = _frames_from_reconstruction(rec, rgb_dir, depth_dir, depths, (info.width, info.height),
                                             index_of, time_of, cfg, notes)
        fmeta = replace(meta, capture_id=capture_id, warnings=[])
        fragments.append(Fragment(f"piece_{k}", _frameset(fmeta, frames, cache_root / capture_id),
                                  "; ".join(notes)))
    return meta, fragments


# ---------- multi-view poses (DA3) ----------

_MV = None


def _multiview():
    """The DA3 model, or None when the package is not installed (falls back to classical matching)."""
    global _MV
    if _MV is None:
        try:
            from scan.multiview import MultiViewModel

            _MV = MultiViewModel()
        except ImportError:
            _MV = False
    return _MV or None


def _cached_infer(mv, images: list[Path], path: Path, process_res: int = PHOTO_DA3_RES):
    from scan.multiview import MultiViewResult

    key = np.array([p.name for p in images] + [f"res={process_res}"])
    if path.exists():
        z = np.load(path)
        if "names" in z.files and list(z["names"]) == list(key):
            return MultiViewResult(z["depth"].astype(np.float32), z["conf"].astype(np.float32), z["T"], z["K"])
    r = mv.infer(images, process_res=process_res)
    np.savez_compressed(path, names=key, depth=r.depth.astype(np.float16), conf=r.conf.astype(np.float16),
                        T=r.T_world_cam, K=r.K)
    return MultiViewResult(r.depth, r.conf, r.T_world_cam, r.K)


def _cached_metric(mv, images: list[Path], result, path: Path, n: int = PHOTO_METRIC_N) -> list[float]:
    path = path.with_name(f"{path.stem}_n{n}_r{result.depth.shape[2]}.npy")  # depends on n and DA3 size
    if path.exists():
        return [float(x) for x in np.load(path)]
    ratios = mv.metric_scale(images, result, n=n)
    np.save(path, np.array(ratios))
    return ratios


def _frames_multiview(mv, images: list[Path], metric: list[np.ndarray], indices: list[int], times: list[float],
                      full_size: tuple[int, int], cfg: VideoConfig, notes: list[str], room_hint: str | None = None,
                      result=None, poses=None, chunk_scale=None,
                      depth_size: tuple[int, int] | None = None, ratios: list[float] | None = None) -> list[Frame]:
    """Frames from DA3 poses + depth; metric scale from the metric depth model.

    Either run DA3 on ``images`` here, or pass ``result`` (one MultiViewResult) or ``poses`` /
    ``chunk_scale`` (joined chunks: per frame pose and the factor applied to its chunk).
    """
    from scan.multiview import MultiViewResult  # noqa: F401

    if result is None and poses is None:
        result = mv.infer(images)
    W, H = full_size
    out_depth = depth_size or ((512, 384) if W >= H else (384, 512))
    given = ratios is not None
    ratios, items = (list(ratios) if given else []), []
    for k in range(len(images)):
        if result is not None:
            d3, conf, T, K3 = result.depth[k], result.conf[k], result.T_world_cam[k], result.K[k]
            f = 1.0
        else:
            d3, conf, T, K3 = poses[k]
            f = chunk_scale[k]
        d3 = d3 * f
        if not given:
            dm = cv2.resize(metric[k], (d3.shape[1], d3.shape[0]), interpolation=cv2.INTER_AREA)
            ok = np.isfinite(dm) & (d3 > 0.05)
            if ok.sum() > 100:
                ratios.append(float(np.median(dm[ok] / d3[ok])))
        items.append((d3, conf, T, K3))
    scale = float(np.median(ratios))
    spread = float(np.median(np.abs(np.array(ratios) / scale - 1)))
    source = "DA3 metric model" if given else "depth model"
    notes.append(f"{len(images)} images posed by DA3; metric scale x{scale:.3f} from the {source} "
                 f"(frame-to-frame spread {spread:.0%})")
    frames = []
    for k, (d3, conf, T, K3) in enumerate(items):
        sx, sy = W / d3.shape[1], H / d3.shape[0]
        intr = Intrinsics(K3[0, 0] * sx, K3[1, 1] * sy, (K3[0, 2] + 0.5) * sx - 0.5, (K3[1, 2] + 0.5) * sy - 0.5, W, H)
        depth = cv2.resize(d3 * scale, out_depth, interpolation=cv2.INTER_AREA).astype(np.float32)
        c = cv2.resize(conf, out_depth, interpolation=cv2.INTER_AREA)
        conf8 = _confidence(depth)
        conf8[c < np.percentile(c, 20)] = 0
        Tm = T.copy()
        Tm[:3, 3] *= scale
        frames.append(Frame(indices[k], times[k], intr, Tm, room_hint=room_hint, depth_size=out_depth,
                            _rgb=lambda p=images[k]: read_rgb(p), _depth=lambda d=depth: d,
                            _confidence=lambda c=conf8: c,
                            _depth_sigma=lambda s=(cfg.depth_rel_sigma * depth).astype(np.float32): s))
    return frames


def video_fragments_da3(mv, video: Path, capture_id: str, cache_root: Path, device: str | None,
                        cfg: VideoConfig | None = None, n_frames: int = 160, chunk: int = 32, overlap: int = 16,
                        process_res: int = 336, per_chunk: bool = True) -> tuple[CaptureMeta, list[Fragment]]:
    """Video -> fragment(s) with DA3 poses.

    Default (fix-loop attempt 2): overlapping chunks of 32 keyframes chained by shared frames, each
    chunk measured as its own fragment at its own metric scale and placed with the chained poses.
    ``chunk == n_frames`` runs one DA3 pass over the whole video (attempt 3: fast, but sparse frames
    let DA3 superimpose different rooms); ``per_chunk=False`` fuses all chunks (attempt 1: scale
    drift of 13-33 % between chunks smears walls). See docs/fix_loop.md.
    """
    from scan.multiview import MultiViewResult, join_chunks

    cfg = cfg or VideoConfig()
    info = probe(video)
    meta = CaptureMeta(capture_id, Tier.VIDEO, "video_file", video.parent, device_model=device,
                       n_frames_raw=info.n_frames, duration_s=info.n_frames / info.fps)
    if device is None:
        meta.warn("video files do not reliably record the iPhone model; pass --device")
    cache = cache_root / capture_id / "video"
    rgb_dir = cache / "rgb"
    manifest = cache / "keyframes.json"
    sig = {"video": video.name, "size": video.stat().st_size, "target": cfg.target_keyframes}
    if manifest.exists() and json.loads(manifest.read_text())["sig"] == sig:
        keys = [tuple(k) for k in json.loads(manifest.read_text())["keys"]]
    else:
        keys = select_keyframes(video, cfg.target_keyframes, rgb_dir)
        manifest.write_text(json.dumps({"sig": sig, "keys": keys}))
    picks = np.unique(np.linspace(0, len(keys) - 1, min(n_frames, len(keys))).round().astype(int))
    keys = [keys[k] for k in picks]
    images = [rgb_dir / f"{i:06d}.jpg" for i, _ in keys]
    da3_dir = cache / f"da3_{len(keys)}_{chunk}_{overlap}_{process_res}"
    da3_dir.mkdir(parents=True, exist_ok=True)
    step = chunk - overlap
    starts = list(range(0, max(len(keys) - overlap, 1), step))
    chunks, metric = [], []
    for c, a in enumerate(starts):
        ids = list(range(a, min(a + chunk, len(keys))))
        path = da3_dir / f"chunk_{c:03d}.npz"
        if path.exists():
            z = np.load(path)
            res = MultiViewResult(z["depth"].astype(np.float32), z["conf"].astype(np.float32), z["T"], z["K"])
            ratio = float(z["metric"])
        else:
            res = mv.infer([images[i] for i in ids], process_res=process_res)
            # metric scale: 4 frames for a single pass, 1 per chunk otherwise (DA3METRIC is slow on CPU)
            rs = mv.metric_scale([images[i] for i in ids], res, n=4 if len(starts) == 1 else 1)
            ratio = float(np.median(rs))
            np.savez_compressed(path, depth=res.depth.astype(np.float16), conf=res.conf.astype(np.float16),
                                T=res.T_world_cam, K=res.K, metric=ratio)
        chunks.append((ids, res))
        metric.append(ratio)
    poses, scales, notes = join_chunks(chunks)
    # metric ratio per chunk, expressed in the joint frame's units
    chunk_scale = [scales[ids[-1]] for ids, _ in chunks]  # last frame: posed by this chunk, not the previous
    joint = [m / s for m, s in zip(metric, chunk_scale)]
    scale = float(np.median(joint))
    drift = float(np.max(np.abs(np.array(joint) / scale - 1)))
    meta.warn(f"DA3 poses: {len(keys)} keyframes in {len(chunks)} chunks; metric scale per chunk varies up to "
              f"{drift:.0%} along the video (scale drift)")
    for n in notes:
        meta.warn(n)
    items, frame_scale = [], []
    for ids, res in chunks:
        for k, i in enumerate(ids):
            if len(items) > i:
                continue
            items.append((res.depth[k], res.conf[k], poses[i], res.K[k]))
            frame_scale.append(scales[i])
    notes2: list[str] = []
    frames = _frames_multiview(mv, images, None, [i for i, _ in keys], [t for _, t in keys],
                               (info.width, info.height), cfg, notes2, poses=items, chunk_scale=frame_scale,
                               depth_size=(384, 216), ratios=[scale])
    frames = _gravity_align(frames)
    fmeta = replace(meta, warnings=[])
    if not per_chunk:
        return meta, [Fragment("video", _frameset(fmeta, frames, cache_root / capture_id), "; ".join(notes2))]
    # Each chunk measured on its own: DA3 is self-consistent within a chunk and the chunk's metric scale
    # is measured for it, so no depth from chunks at disagreeing scales is fused. The chained poses
    # serve only to lay the rooms out.
    layout = {f.index: f.T_world_cam[[0, 2], 3] for f in frames}
    out = []
    for c, ((ids, res), ratio) in enumerate(zip(chunks, metric)):
        n: list[str] = []
        fr = _frames_multiview(mv, [images[i] for i in ids], None, [keys[i][0] for i in ids],
                               [keys[i][1] for i in ids], (info.width, info.height), cfg, n, result=res,
                               depth_size=(384, 216), ratios=[ratio])
        fr = _gravity_align(fr)
        out.append(Fragment(f"chunk_{c}", _frameset(replace(meta, warnings=[]), fr, cache_root / capture_id),
                            "; ".join(n), layout_xz={f.index: layout[f.index] for f in fr}))
    meta.warn(f"video measured per chunk: {len(out)} chunks, each with its own metric scale")
    return meta, out


# ---------- photos ----------

def _rigid_fit(A: np.ndarray, B: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """R, t minimising |R B + t - A| (Kabsch)."""
    ca, cb = A.mean(axis=0), B.mean(axis=0)
    U, _, Vt = np.linalg.svd((B - cb).T @ (A - ca))
    D = np.diag([1, 1, np.sign(np.linalg.det(Vt.T @ U.T))])
    R = Vt.T @ D @ U.T
    return R, ca - R @ cb


def _lift(kp: np.ndarray, depth: np.ndarray, K: Intrinsics) -> np.ndarray:
    dh, dw = depth.shape
    u = np.clip((kp[:, 0] * dw / K.width).astype(int), 0, dw - 1)
    v = np.clip((kp[:, 1] * dh / K.height).astype(int), 0, dh - 1)
    z = depth[v, u]
    return np.column_stack([(kp[:, 0] - K.cx) / K.fx * z, (kp[:, 1] - K.cy) / K.fy * z, z])


def register_photos(paths: list[Path], depths: list[np.ndarray], K: Intrinsics,
                    min_inliers: int = 25, iters: int = 800, seed: int = 0):
    """Relative poses of a room's photos from SIFT matches lifted to 3D by metric depth.

    Room photos are usually taken turning on the spot: no baseline, so structure from motion
    cannot triangulate. With depth every match is a 3D-3D pair and a rigid fit works for any
    motion, rotation included. Pairs -> maximum spanning tree -> poses in the first photo's frame.
    """
    rng = np.random.default_rng(seed)
    sift = cv2.SIFT_create(4000)
    feats = []
    for p in paths:
        g = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        kp, des = sift.detectAndCompute(g, None)
        feats.append((np.array([k.pt for k in kp], np.float32), des))
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    n = len(paths)
    edges = []
    for i in range(n):
        for j in range(i + 1, n):
            if feats[i][1] is None or feats[j][1] is None:
                continue
            m = [a for a, b in matcher.knnMatch(feats[j][1], feats[i][1], k=2) if a.distance < 0.75 * b.distance]
            if len(m) < min_inliers:
                continue
            Xj = _lift(feats[j][0][[x.queryIdx for x in m]], depths[j], K)
            Xi = _lift(feats[i][0][[x.trainIdx for x in m]], depths[i], K)
            ok = np.isfinite(Xi).all(axis=1) & np.isfinite(Xj).all(axis=1) & (Xi[:, 2] > 0.2) & (Xj[:, 2] > 0.2)
            Xi, Xj = Xi[ok], Xj[ok]
            if len(Xi) < min_inliers:
                continue
            tol = np.maximum(0.05, 0.06 * Xi[:, 2])
            best = None
            for _ in range(iters):
                s = rng.choice(len(Xi), 3, replace=False)
                R, t = _rigid_fit(Xi[s], Xj[s])
                inl = np.linalg.norm(Xj @ R.T + t - Xi, axis=1) < tol
                if best is None or inl.sum() > best.sum():
                    best = inl
            if best.sum() < min_inliers:
                continue
            for _ in range(2):
                R, t = _rigid_fit(Xi[best], Xj[best])
                best = np.linalg.norm(Xj @ R.T + t - Xi, axis=1) < tol
            T = np.eye(4)
            T[:3, :3], T[:3, 3] = R, t  # photo j camera -> photo i camera
            edges.append((int(best.sum()), i, j, T))
    # maximum spanning tree grown from the best-connected photo
    deg = np.zeros(n)
    for w, i, j, _ in edges:
        deg[i] += w
        deg[j] += w
    root = int(np.argmax(deg))
    poses: list[np.ndarray | None] = [None] * n
    poses[root] = np.eye(4)
    grown = True
    while grown:
        grown = False
        cand = [(w, i, j, T) for w, i, j, T in edges if (poses[i] is None) != (poses[j] is None)]
        if cand:
            w, i, j, T = max(cand, key=lambda e: e[0])
            if poses[i] is not None:
                poses[j] = poses[i] @ T
            else:
                poses[i] = poses[j] @ np.linalg.inv(T)
            grown = True
    used = sum(p is not None for p in poses)
    return poses, f"{used} of {n} photos registered by depth-lifted matches ({len(edges)} linked pairs)"


def photo_fragments(room_dirs: dict[str, Path], capture_id: str, cache_root: Path, device: str | None,
                    cfg: VideoConfig | None = None) -> tuple[CaptureMeta, list[Fragment]]:
    cfg = cfg or VideoConfig()
    first = next(iter(room_dirs.values()))
    n_photos = sum(len([p for p in d.iterdir() if p.is_file()]) for d in room_dirs.values())
    meta = CaptureMeta(capture_id, Tier.PHOTO, "photo_folders", first.parent, n_frames_raw=n_photos)
    model = None
    fragments = []
    next_index = 0
    for room, folder in room_dirs.items():
        paths = sorted(p for p in folder.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".heic", ".heif"})
        info = photo_info(paths[0])
        if meta.device_model is None:
            meta.device_model = device or info.model
        if info.focal_px and 2 * np.degrees(np.arctan(info.width / 2 / info.focal_px)) > 90:
            meta.warn(f"{room}: photos taken with the ultra-wide (0.5x) lens; DA3 misjudges its focal length, so "
                      "sizes may be under-measured by up to ~11 % (protocol: use the 1x lens)")
        cache = cache_root / capture_id / "photo" / room
        img_dir = cache / "images"
        img_dir.mkdir(parents=True, exist_ok=True)
        names = []
        for p in paths:
            name = p.stem + ".jpg"
            if not (img_dir / name).exists():
                cv2.imwrite(str(img_dir / name), cv2.cvtColor(read_photo(p, PHOTO_MAX_SIDE), cv2.COLOR_RGB2BGR),
                            [cv2.IMWRITE_JPEG_QUALITY, 95])
            names.append(name)
        h, w = cv2.imread(str(img_dir / names[0])).shape[:2]
        focal = (info.focal_px or 1.2 * max(info.width, info.height)) * w / info.width
        params = f"{focal},{w / 2},{h / 2},0"
        depth_size = (256, 192) if w >= h else (192, 256)
        depths_all = {}
        for name in names if _multiview() is None else []:  # fallback path only: DA3 gives its own depth
            p = cache / (Path(name).stem + ".depth.npy")
            if not p.exists():
                model = model or DepthModel()
                np.save(p, model.predict(read_rgb(img_dir / name), depth_size).astype(np.float16))
            depths_all[name] = np.load(p).astype(np.float32)
        # per-room numbering and a per-room cache folder: label / damage caches are keyed by frame
        # index, so indices must mean the same photo whichever rooms are processed together
        index_of = {n: k for k, n in enumerate(names)}
        time_of = {n: float(index_of[n]) for n in names}
        next_index += len(names)
        notes: list[str] = []
        frames = []
        mv = _multiview()
        if mv is not None:
            images = [img_dir / n for n in names]
            result = _cached_infer(mv, images, cache / "da3.npz")
            ratios = _cached_metric(mv, images, result, cache / "da3_metric_scale.npy")
            frames = _frames_multiview(mv, images, None, [index_of[n] for n in names], [time_of[n] for n in names],
                                       (w, h), cfg, notes, room_hint=room, result=result, ratios=ratios)
        else:
            K = Intrinsics(focal, focal, w / 2, h / 2, w, h)
            poses, reg_note = register_photos([img_dir / n for n in names], [depths_all[n] for n in names], K)
            notes.append(reg_note + " (DA3 not installed)")
            for k, name in enumerate(names):
                if poses[k] is None:
                    continue
                d = depths_all[name]
                frames.append(Frame(index_of[name], time_of[name], K, poses[k], room_hint=room,
                                    depth_size=depth_size, _rgb=lambda p=img_dir / name: read_rgb(p),
                                    _depth=lambda d=d: d, _confidence=lambda c=_confidence(d): c,
                                    _depth_sigma=lambda s=(cfg.depth_rel_sigma * d).astype(np.float32): s))
        frames = _gravity_align(frames)
        fmeta = CaptureMeta(capture_id, Tier.PHOTO, "photo_folders", folder, device_model=meta.device_model)
        fs = FrameSet(meta=fmeta, frames=frames, cache_dir=cache, trajectory=None)
        fragments.append(Fragment(room, fs, "; ".join(notes)))
    return meta, fragments


# ---------- per-fragment pipeline ----------

@dataclass
class FragmentRun:
    fragment: Fragment
    res: object  # PipelineResult up to openings
    rooms: list  # RoomMeasurement kept from this fragment (fragment coordinates)
    openings: list  # Opening of those rooms
    placed: bool = False
    rot: float = 0.0  # radians, fragment xz -> global xz
    t: np.ndarray = field(default_factory=lambda: np.zeros(2))
    how: str = ""


def run_fragment(frag: Fragment, keep_largest: bool) -> FragmentRun | None:
    from scan.pipeline import run_pipeline

    from scan.openings import find_openings

    try:
        res = run_pipeline(frag.frameset, upto="measure", drift=False)
        attached = attach_wall_planes(res.rooms, res.model, res.layout.floor_map.frame)
        if attached:
            frag.note += f"; {attached} outline edge(s) linked to nearby wall planes"
        res.openings = find_openings(res.model, res.layout, res.rooms, res.labelled)
    except Exception as exc:  # a fragment too small to give a room must not stop the capture
        frag.note += f"; no room ({type(exc).__name__}: {exc})"
        try:
            from scan.qc import run_qc

            cov = run_qc(frag.frameset).report.coverage
            if not cov.floor_seen:
                frag.note += (f"; the photos show only {cov.floor_area_m2 or 0:.1f} m2 of floor: retake from the "
                              "corners, across the room, with the floor along the far wall in view")
        except Exception:
            pass
        return None
    rooms = [r for r in res.rooms if r.floor_area_m2 >= 1.0]
    if not rooms:
        frag.note += "; no room of at least 1 m2"
        return None
    if keep_largest:
        rooms = [max(rooms, key=lambda r: r.floor_area_m2)]
    ids = {r.id for r in rooms}
    return FragmentRun(frag, res, rooms, [o for o in res.openings if o.room_id in ids])


def attach_wall_planes(rooms, model, frame, tol_m: float = 0.2, min_overlap_m: float = 0.3) -> int:
    """Link outline edges that have no wall to a parallel fitted wall plane running along them.

    With a handful of photos (or a short video) each wall is seen in pieces, so the room outline
    often comes from the floor's extent rather than from a wall line, and C7b marks the edge
    inferred. If a wall plane facing into the room lies within ``tol_m`` of the edge and runs
    along it, the edge is that wall: link it so C8 searches it for doors and windows.
    """
    n = 0
    for room in rooms:
        pts = np.array(room.corners_uv)
        centre = pts.mean(axis=0)
        for k, wm in enumerate(room.walls):
            if wm.wall_ids or wm.length_m < 0.5:
                continue
            a, b = pts[k], pts[(k + 1) % len(pts)]
            i = 0 if abs(a[0] - b[0]) < 1e-9 else 1  # uv axis constant along the edge
            coord, lo, hi = a[i], min(a[1 - i], b[1 - i]), max(a[1 - i], b[1 - i])
            inward = 1.0 if centre[i] > coord else -1.0
            best = None
            for w in model.walls:
                nuv = frame.xz_to_uv(np.array([w.normal_xz]))[0]
                if nuv[i] * inward < 0.95:
                    continue
                ends = frame.xz_to_uv(np.array([w.start_xz, w.end_xz]))
                pos = float(ends[:, i].mean())
                overlap = min(hi, ends[:, 1 - i].max()) - max(lo, ends[:, 1 - i].min())
                if abs(pos - coord) <= tol_m and overlap >= min_overlap_m:
                    if best is None or overlap > best[0]:
                        best = (overlap, w)
            if best is not None:
                wm.wall_ids = [best[1].id]
                wm.inferred = False
                wm.coverage = round(min(1.0, best[0] / max(wm.length_m, 1e-6)), 3)
                n += 1
    return n


# ---------- placing fragments by doors ----------

@dataclass(frozen=True)
class StitchFragmentsConfig:
    width_tol_m: float = 0.20  # the two sides of one door measure within this
    wall_thickness_m: float = 0.10  # assumed internal wall thickness when placing through a door
    max_overlap_m2: float = 0.8  # a placement overlapping placed rooms by more is rejected
    confirm_m: float = 0.5  # another door pair this close after placement confirms it
    raster_m: float = 0.05


def _rot(a: float) -> np.ndarray:
    return np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])


def _door_frame(room, op) -> tuple[np.ndarray, np.ndarray]:
    """Opening centre and the outward unit normal of its wall (fragment xz)."""
    c = np.array(room.corners_xz, float)
    k = op.wall_index
    d = c[(k + 1) % len(c)] - c[k]
    n = np.array([d[1], -d[0]]) / max(np.linalg.norm(d), 1e-9)
    mid = (c[k] + c[(k + 1) % len(c)]) / 2
    if n @ (mid - c.mean(axis=0)) < 0:
        n = -n
    return np.array(op.center_xz, float), n


def _doors(run: FragmentRun):
    by_id = {r.id: r for r in run.rooms}
    out = []
    for op in run.openings:
        if op.kind == "window" or op.room_id not in by_id:
            continue
        c, n = _door_frame(by_id[op.room_id], op)
        out.append((op, c, n))
    return out


def _poly_mask(polys: list[np.ndarray], origin: np.ndarray, shape, res: float) -> np.ndarray:
    m = np.zeros(shape, np.uint8)
    for p in polys:
        cv2.fillPoly(m, [np.round((p - origin) / res).astype(np.int32)], 1)
    return m


def _overlap_m2(a: list[np.ndarray], b: list[np.ndarray], res: float) -> float:
    allp = np.concatenate(a + b)
    origin = allp.min(axis=0) - 0.1
    shape = tuple((np.ceil((allp.max(axis=0) - origin) / res) + 2).astype(int)[::-1])
    # shrink by half a wall so rooms sharing a wall face don't count as overlapping
    k = np.ones((3, 3), np.uint8)
    ma = cv2.erode(_poly_mask(a, origin, shape, res), k)
    mb = cv2.erode(_poly_mask(b, origin, shape, res), k)
    return float((ma & mb).sum() * res * res)


def place_fragments(runs: list[FragmentRun], cfg: StitchFragmentsConfig | None = None) -> list[str]:
    """Greedy: anchor the largest fragment, then repeatedly add the fragment whose door best
    matches an already placed door (opposite normals, similar width, no overlap)."""
    cfg = cfg or StitchFragmentsConfig()
    log = []
    if not runs:
        return log
    anchor = max(runs, key=lambda r: sum(m.floor_area_m2 for m in r.rooms))
    anchor.placed, anchor.how = True, "anchor"

    def polys(run, rot, t):
        R = _rot(rot)
        return [np.array(m.corners_xz, float) @ R.T + t for m in run.rooms]

    def placed_doors():
        out = []
        for r in runs:
            if r.placed:
                R = _rot(r.rot)
                out += [(r, op, R @ c + r.t, R @ n) for op, c, n in _doors(r)]
        return out

    while True:
        best = None
        pd = placed_doors()
        placed_polys = [p for r in runs if r.placed for p in polys(r, r.rot, r.t)]
        for run in runs:
            if run.placed:
                continue
            doors = _doors(run)
            for opB, cB, nB in doors:
                for runA, opA, cA, nA in pd:
                    if abs(opA.width_m - opB.width_m) > cfg.width_tol_m:
                        continue
                    rot = np.arctan2(-nA[1], -nA[0]) - np.arctan2(nB[1], nB[0])
                    R = _rot(rot)
                    t = cA + nA * cfg.wall_thickness_m - R @ cB
                    ov = _overlap_m2(placed_polys, polys(run, rot, t), cfg.raster_m)
                    if ov > cfg.max_overlap_m2:
                        continue
                    confirm = 0
                    for op2, c2, n2 in doors:
                        if op2 is opB:
                            continue
                        c2g, n2g = R @ c2 + t, R @ n2
                        if any(np.linalg.norm(c2g - c) <= cfg.confirm_m + cfg.wall_thickness_m and n2g @ n < -0.9
                               for _, o, c, n in pd if o is not opA):
                            confirm += 1
                    score = confirm - 2.0 * ov - 3.0 * abs(opA.width_m - opB.width_m)
                    if best is None or score > best[0]:
                        best = (score, run, rot, t, runA, opA, opB, ov, confirm)
        if best is None:
            break
        score, run, rot, t, runA, opA, opB, ov, confirm = best
        run.placed, run.rot, run.t = True, float(rot), t
        run.how = (f"door {opB.width_m:.2f} m matched to {runA.fragment.name} door {opA.width_m:.2f} m "
                   f"(overlap {ov:.2f} m2, {confirm} more door(s) agree)")
        log.append(f"{run.fragment.name}: {run.how}")
    # fragments with no matching door: lay them out to the right, unconnected
    placed_polys = [p for r in runs if r.placed for p in polys(r, r.rot, r.t)]
    x0 = max(p[:, 0].max() for p in placed_polys) + 1.5
    for run in runs:
        if run.placed:
            continue
        # keep the fragment's own Manhattan axes parallel to the anchor's
        a = anchor.res.layout.manhattan_deg - run.res.layout.manhattan_deg
        run.rot = float(np.radians(a))
        P = polys(run, run.rot, np.zeros(2))
        lo = np.min([p.min(axis=0) for p in P], axis=0)
        run.t = np.array([x0 - lo[0], -lo[1] + min(p[:, 1].min() for p in placed_polys)])
        x0 += np.max([p.max(axis=0) for p in P], axis=0)[0] - lo[0] + 1.0
        run.how = "no matching door: placed apart, adjacency unknown"
        log.append(f"{run.fragment.name}: {run.how}")
    return log


# ---------- one result from placed fragments ----------

def _xf(run: FragmentRun, xz) -> np.ndarray:
    return np.atleast_2d(np.asarray(xz, float)) @ _rot(run.rot).T + run.t


def _wall_axis(corners_uv: np.ndarray, k: int) -> int:
    a, b = corners_uv[k], corners_uv[(k + 1) % len(corners_uv)]
    return 0 if abs(a[0] - b[0]) < 1e-9 else 1  # the uv axis that is constant along the wall


def _move_damage(d, run: FragmentRun, f_frame, g_frame, room_f, room_g):
    """Damage geometry from fragment uv to global uv (outline / centroid are uv for floors and
    ceilings; for walls the first coordinate is the uv position along the wall)."""
    def to_g(uv):
        return g_frame.xz_to_uv(_xf(run, f_frame.uv_to_xz(np.atleast_2d(uv))))

    if d.surface_kind in ("floor", "ceiling"):
        if d.outline_2d:
            d.outline_2d = [tuple(map(float, p)) for p in to_g(np.array(d.outline_2d))]
        d.centroid_2d = tuple(map(float, to_g(np.array(d.centroid_2d))[0]))
        return d
    cf, cg = np.array(room_f.corners_uv), np.array(room_g.corners_uv)
    k = d.wall_index
    af, ag = _wall_axis(cf, k), _wall_axis(cg, k)

    def along(x):
        p = np.zeros(2)
        p[af] = cf[k][af]
        p[1 - af] = x
        return float(to_g(p)[0][1 - ag])

    o, s = along(0.0), along(1.0) - along(0.0)
    d.centroid_2d = (o + s * d.centroid_2d[0], d.centroid_2d[1])
    d.outline_2d = [(o + s * x, y) for x, y in d.outline_2d]
    return d


def assemble(meta: CaptureMeta, runs: list[FragmentRun], log: list[str], damage: bool = True):
    """Placed fragments -> one PipelineResult-like object for build_result / render_floor_plan."""
    from types import SimpleNamespace

    from scan.drift import DriftReport
    from scan.pipeline import PipelineResult
    from scan.qc.report import Issue
    from scan.rooms.floormap import GridFrame
    from scan.stitch import stitch

    anchor = next(r for r in runs if r.how == "anchor")
    g_angle = anchor.res.layout.manhattan_deg
    g_frame = GridFrame(g_angle, (0.0, 0.0), 0.05, (1, 1))
    rooms, openings, regions = [], [], []
    rid = oid = 0
    for run in runs:
        f_frame = run.res.layout.floor_map.frame
        id_map = {}
        for r in run.rooms:
            rid += 1
            g = copy.deepcopy(r)
            g.id = rid
            if meta.tier is Tier.PHOTO:
                g.name = run.fragment.name.lower()
            else:
                g.name = f"room_{rid}"
            xz = _xf(run, r.corners_xz)
            g.corners_xz = [tuple(map(float, p)) for p in xz]
            g.corners_uv = [tuple(map(float, p)) for p in g_frame.xz_to_uv(xz)]
            id_map[r.id] = g
            rooms.append(g)
        for o in run.openings:
            oid += 1
            g = copy.deepcopy(o)
            g.id, g.room_id, g.room_name = oid, id_map[o.room_id].id, id_map[o.room_id].name
            g.center_xz = tuple(map(float, _xf(run, o.center_xz)[0]))
            g.connects_room = None
            openings.append(g)
        if damage:
            from scan.damage import detect_damage

            local = []
            for r in run.rooms:  # fragment geometry, global names: surface ids come out global
                lr = copy.deepcopy(r)
                lr.id, lr.name = id_map[r.id].id, id_map[r.id].name
                local.append(lr)
            try:
                dr = detect_damage(run.res.labelled, local, run.res.model, f_frame)
            except Exception as exc:
                log.append(f"{run.fragment.name}: damage detection failed ({type(exc).__name__}: {exc})")
                continue
            for d in dr.regions:
                regions.append(_move_damage(d, run, f_frame, g_frame,
                                            next(r for r in local if r.id == d.room_id), id_map_by_gid(id_map, d.room_id)))
    for k, d in enumerate(regions):
        d.id = k
    layout = SimpleNamespace(capture_id=meta.capture_id, manhattan_deg=g_angle, doorways=[],
                             floor_map=SimpleNamespace(frame=g_frame))
    plan = stitch(rooms, openings, layout)

    # QC: the anchor's report with frame counts of all fragments, plus the stitching log
    reports = [r.res.qc.report for r in runs]
    qc = copy.deepcopy(anchor.res.qc.report)
    qc.capture_id = meta.capture_id
    qc.n_frames_in = sum(r.n_frames_in for r in reports)
    qc.n_frames_kept = sum(r.n_frames_kept for r in reports)
    qc.quality_score = round(float(np.average([r.quality_score for r in reports],
                                              weights=[max(r.n_frames_kept, 1) for r in reports])), 3)
    unplaced = [r.fragment.name for r in runs if not r.placed and r.rooms]
    if unplaced:
        qc.issues.append(Issue("fragments_unconnected", "warning",
                               f"{len(unplaced)} part(s) could not be placed through a door: {', '.join(unplaced)}; "
                               "their dimensions are valid but their position and adjacency are not",
                               "capture each doorway from both sides, keeping the door frame in view"))
    for line in log:
        meta.warn(f"stitch: {line}")
    for run in runs:
        if run.fragment.note:
            meta.warn(f"{run.fragment.name}: {run.fragment.note}")
    labelled_frames = [f for r in runs for f in r.res.labelled.frames]
    res = PipelineResult(FrameSet(meta, labelled_frames))
    res.qc = SimpleNamespace(report=qc)
    res.drift = DriftReport(False, note="not applicable: parts are placed by matching doors (fragment stitcher)")
    res.labelled = FrameSet(meta, labelled_frames)
    res.layout = layout
    res.rooms, res.openings, res.plan = rooms, openings, plan
    res.damage = SimpleNamespace(regions=regions) if damage else None
    if damage:
        from scan.rules import apply_rules

        res.flags, res.scope = apply_rules(regions, plan.rooms)
    return res


def id_map_by_gid(id_map: dict, gid: int):
    return next(g for g in id_map.values() if g.id == gid)


def place_by_layout(runs: list[FragmentRun]) -> list[str]:
    """Place fragments from camera centres known in a common frame (video chunks).

    Rotation: 2D Procrustes on the shared cameras, snapped so the fragment's Manhattan axes are
    parallel to the anchor's (rooms stay axis-aligned in the plan); translation re-fitted after snapping.
    """
    anchor = max(runs, key=lambda r: sum(m.floor_area_m2 for m in r.rooms))
    g_m = anchor.res.layout.manhattan_deg
    log = []
    for run in runs:
        frames = run.res.labelled.frames
        ids = [f.index for f in frames if f.index in run.fragment.layout_xz]
        if len(ids) < 2:
            run.how = "no layout cameras"
            continue
        A = np.array([run.fragment.layout_xz[i] for i in ids])  # common frame
        B = np.array([f.T_world_cam[[0, 2], 3] for f in frames if f.index in run.fragment.layout_xz])
        a0, b0 = A - A.mean(axis=0), B - B.mean(axis=0)
        ang = np.arctan2((b0[:, 0] * a0[:, 1] - b0[:, 1] * a0[:, 0]).sum(), (b0 * a0).sum())
        base = np.radians(g_m - run.res.layout.manhattan_deg)
        k = np.round((ang - base) / (np.pi / 2))
        run.rot = float(base + k * np.pi / 2)
        run.t = A.mean(axis=0) - _rot(run.rot) @ B.mean(axis=0)
        run.placed = True
        run.how = "anchor" if run is anchor else "placed from the chained camera poses"
        log.append(f"{run.fragment.name}: rotation {np.degrees(ang):.1f} deg snapped to {np.degrees(run.rot):.1f} deg")
    return log


def drop_duplicates(runs: list[FragmentRun], min_share: float = 0.5) -> list[str]:
    """Where two fragments measured the same room (overlap > min_share of the smaller), keep the copy
    with more observed walls (then the larger)."""
    items = []
    for run in runs:
        R = _rot(run.rot)
        for m in run.rooms:
            items.append((run, m, np.array(m.corners_xz, float) @ R.T + run.t))
    score = lambda m: (sum(not w.inferred for w in m.walls), m.floor_area_m2)
    items.sort(key=lambda it: score(it[1]), reverse=True)
    kept, log = [], []
    for run, m, P in items:
        dup = next((k for k in kept if _overlap_m2([k[2]], [P], 0.05) > min_share * min(m.floor_area_m2,
                                                                                     k[1].floor_area_m2)), None)
        if dup is None:
            kept.append((run, m, P))
        else:
            log.append(f"{run.fragment.name}: room of {m.floor_area_m2:.1f} m2 duplicates one from "
                       f"{dup[0].fragment.name}; kept the better-observed copy")
    keep = {id(m) for _, m, _ in kept}
    for run in runs:
        ids = {m.id for m in run.rooms if id(m) in keep}
        run.rooms = [m for m in run.rooms if m.id in ids]
        run.openings = [o for o in run.openings if o.room_id in ids]
    return log


def run_capture(meta: CaptureMeta, fragments: list[Fragment], damage: bool = True):
    keep_largest = meta.tier is Tier.PHOTO
    runs = [r for r in (run_fragment(f, keep_largest) for f in fragments) if r is not None]
    dropped = [f.name for f in fragments if not any(r.fragment is f for r in runs)]
    if not runs:
        raise RuntimeError("no fragment produced a room")
    if all(r.fragment.layout_xz for r in runs):
        log = place_by_layout(runs)
        runs = [r for r in runs if r.placed]
        log += drop_duplicates(runs)
    else:
        log = place_fragments(runs)
    if dropped:
        log.append(f"no room from: {', '.join(dropped)}")
        for f in fragments:
            if f.name in dropped:
                meta.warn(f"{f.name}: {f.note.lstrip('; ')}")
    return assemble(meta, runs, log, damage)
