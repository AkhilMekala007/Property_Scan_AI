# LLD 01 — C1 Ingest + C2 LiDAR adapter

**Goal:** turn any capture folder into the shared `FrameSet` format (HLD §5.1). This block implements tier detection for all three tiers and the full adapter for the LiDAR tier (Stray Scanner exports).

## Modules

| File | Responsibility |
|---|---|
| `scan/core/types.py` | Shared format: `Tier`, `Intrinsics`, `CaptureMeta`, `Frame`, `FrameSet`; project-wide conventions |
| `scan/core/geometry.py` | Quaternion → rotation, pose matrices, back-projection, point transforms |
| `scan/ingest/detect.py` | **C1**: decide tier and source format from the files alone |
| `scan/ingest/load.py` | `load_capture(path)`: detect → pick adapter → `FrameSet` |
| `scan/adapters/base.py` | Adapter interface + config (keyframe rule, LiDAR noise model, device override) |
| `scan/adapters/stray.py` | **C2**: Stray Scanner export → `FrameSet` |
| `scan/io/video.py` | Single-pass frame extraction with an on-disk cache |
| `scan/debug/topdown.py` | Top-down render used to sanity-check conventions |
| `scan/cli.py` | `scan inspect <capture> [--topdown] [--device ...]` |

## Conventions

- Metres and seconds. Invalid depth is `NaN`, never 0.
- Camera frame: OpenCV (+x right, +y down, +z forward). World: gravity aligned, +y up.
- `Frame.T_world_cam` maps camera points to world points.
- Stray Scanner poses already follow this convention (verified: floors land ~1.4 m below the handheld phone on all three sample captures), so no axis flips are applied.
- Intrinsics are stored at RGB resolution; `Frame.depth_intrinsics` rescales them to the depth map (pixel centres at integer coordinates).

## Tier detection (C1)

| Folder contains | Result |
|---|---|
| `rgb.mp4` + `odometry.csv` + `camera_matrix.csv` + `depth/` | LiDAR, `stray_scanner` |
| exactly one `.mov` / `.mp4` / `.m4v` (or the video file itself) | Video, `video_file` |
| sub-folders containing images | Photo, `photo_folders`, one room per folder |
| a flat folder of images | Photo, single room, with a warning |
| several videos, or nothing recognisable | `CaptureFormatError` with the expected layouts |

Single wrapper folders are stepped into (`single_room/` → `single_room/c00a170fe1/`). Hidden files are ignored.

## LiDAR adapter (C2)

1. **Parse** `odometry.csv` (header-driven; per-frame intrinsics used when present, else `camera_matrix.csv`), probe `rgb.mp4`, list `depth/` and `confidence/`.
2. **Validate**, recording problems as warnings: frame counts agree (±1 for the container's count), timestamps increase, `frame` column is 0..N-1, quaternions are unit length, depth maps share one size and are not mostly empty. Inconsistent depth sizes or zero usable frames are errors.
3. **Select keyframes** deterministically: a new keyframe when the camera moved ≥ 10 cm or turned ≥ 5° since the last one; if that exceeds 400, thin evenly across the walk.
4. **Extract** only the keyframes from `rgb.mp4` in one sequential pass into `outputs/cache/<capture_id>/rgb/*.jpg` (quality 95). A manifest (video size, mtime, indices, quality) makes reruns reuse the cache.
5. **Build frames** with lazy loaders for RGB, depth (mm → m, 0 → NaN), confidence and per-pixel depth sigma.

**Depth uncertainty** (placeholder until C12 calibration): σ = 0.005 m + 0.005·z², ×3 where confidence = 1, NaN where confidence = 0 or depth is missing.

**Device model:** Stray exports do not record it, so `device_model` is `None` with a warning unless `--device` is given.

## Results on the sample captures

| Capture | Raw frames | Keyframes | Duration | Camera height above floor | Extent | First load | Cached load |
|---|---|---|---|---|---|---|---|
| `single_room` | 1,715 | 238 | 37 s | 1.40 m | 7.0 × 6.6 m | 17 s | 0.2 s |
| `single_scan_floor_only` | 5,251 | 400 (capped) | 115 s | 1.41 m | 9.9 × 10.1 m | 49 s | — |
| `single_scan_with_ceiling` | 9,745 | 400 (capped) | 215 s | 1.46 m | 11.7 × 10.9 m | 87 s | — |

## Tests

- `tests/test_detect.py`: every detection rule and error path, on tiny fake folders.
- `tests/test_geometry.py`: quaternions, intrinsics scaling, back-projection axes.
- `tests/test_stray_adapter.py`: a synthetic Stray export (generated in `tests/conftest.py`) checks keyframes, depth units, NaN handling, sigma, device warning, count-mismatch warning, cache reuse and determinism.
- Sample-data tests are marked `sample` and skipped by default: `pytest -m sample`.

## Known limits / next

- The 400-keyframe cap is hit on long walks; revisit once geometry runtimes are known.
- IMU data is not used yet.
- Video and photo adapters are registered but raise `NotImplementedError` (next blocks).
