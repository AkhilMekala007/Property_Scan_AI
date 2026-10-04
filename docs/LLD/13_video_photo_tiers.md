# LLD 13 — Video and photo tiers: poses without LiDAR, fragments, door stitching

**Goal:** the same outputs as the LiDAR tier from an iPhone video (no depth sensor) or from a few photos per room.

```
video ─► keyframes (sharpest per window) ─► 160 frames ─► DA3-BASE in overlapping chunks (32, overlap 8)
          ─► chunks chained by a similarity transform through shared cameras ─► one fragment
photos ─► per room folder: DA3-BASE on all photos at once ─► one fragment per room
each fragment ─► metric scale (DA3METRIC-LARGE) ─► gravity ─► C3 QC → C4 labels → C7a → C6 → C7b
             ─► link edges to nearby wall planes ─► C8 openings
fragments ─► placed by matching doors (opposite normals, similar width, no overlap) ─► C9 stitch ─► C10/C11 ─► C13
```

## Why not structure from motion

Both benchmark captures were tried with COLMAP first (`video_fragments`, kept as the fallback):

| Capture | COLMAP result |
|---|---|
| Video, 504 keyframes | 18 disconnected pieces; largest 28 frames (~9 s); none contained enough floor to give a room |
| Photos, 8 per room | "no good initial pair": photos are taken in pairs from a few standpoints (pure rotation, no baseline) |

Depth-lifted SIFT (`register_photos`: matches lifted to 3D with metric depth, RANSAC rigid fit, maximum spanning tree) works for any motion but registered only 4 of 8 photos: matches between standpoints are at noise level on white walls and repetitive wardrobe fronts.

DA3 (Depth Anything 3) predicts depth and every camera's pose for a set of images jointly, without feature tracks. On Room_3 it posed all 8 photos; pairs taken from one standpoint come out at the same position.

## Models (all Apache-2.0)

| Key | Use |
|---|---|
| `da3-base` | Poses + depth (arbitrary scale) for a set of images |
| `da3-metric-large` | Metric scale: metres = focal_px × output / 300. Sampled on 4 images per room / 1 per video chunk (slow on CPU) |
| `depth-anything-v2-metric-indoor-small` | Fallback scale only (focal-blind: over-estimated by ~25 % on the iPhone 15 photos, ceiling 3.67 m vs LiDAR 2.93 m) |

DA3 is installed from GitHub at a pinned commit (`--no-deps`; its API module imports export helpers we stub out — `scan/multiview.py`). Without it the pipeline falls back to COLMAP / depth-lifted matching.

## Modules

| File | Responsibility |
|---|---|
| `scan/multiview.py` | `MultiViewModel.infer` (DA3-BASE), `metric_scale` (DA3METRIC), `join_chunks` (similarity chain) |
| `scan/fragments.py` | Photo / video fragment builders, `run_fragment`, `attach_wall_planes`, `place_fragments`, `assemble`, `run_capture` |
| `scan/io/photos.py` | HEIC/JPEG reading, EXIF focal length |
| `scan/cli.py` | `scan run` branches: LiDAR → `run_pipeline`; video / photos → fragments |

## Key decisions

- **Chunk joining:** rotation from the shared cameras' orientations (SVD of Σ R_a R_bᵀ), then scale and translation from their centres. Orientation-first keeps rotation correct when shared cameras barely move. The per-chunk metric ratio divided by the chunk's chain scale shows **scale drift along the video**; it is reported as a warning.
- **Fusion density (C7a):** estimated-depth tiers fuse every depth pixel and need 2 hits per voxel (LiDAR: stride 2, 3 hits). With 8 photos the LiDAR setting kept 9 voxels.
- **Edges linked to wall planes:** with few views the outline comes from the floor's extent, so C7b marks edges inferred and C8 never searches them for doors. An edge with a parallel wall plane facing into the room within 20 cm and overlapping ≥ 0.3 m is linked to it. Photo / video only; LiDAR unchanged.
- **Door placement:** rotation makes B's door normal opposite A's; translation puts B's door centre one wall thickness (0.10 m) beyond A's. Candidates: widths within 0.20 m, overlap with placed rooms ≤ 0.8 m². Score: other door pairs that coincide after placement, minus overlap and width mismatch. Greedy from the largest fragment. Unplaced fragments are laid out apart and raise the `fragments_unconnected` QC warning: their dimensions stand, their position and adjacency do not.
- **Per-room caches:** label and damage caches are keyed by frame index, so each photo room has its own cache folder and local numbering (a shared folder once gave Hall the labels of Room_3).

## Known limits

- CPU runtime: DA3-BASE ~25 s / image at 504 px; DA3METRIC-LARGE ~60 s / image at 504 px (336 px used).
- Door detection on photos relies on closed-door labels; a room whose door is never photographed square-on cannot be placed.
- No loop closure along the video: chunk-chain error accumulates (reported via the scale-drift warning).

## Speed / accuracy trade-off (measured 2026-10-04, cold cache, i5-1135G7 CPU)

| Photo settings | Cold runtime | Walls in ±8 % | Median wall error | Ceilings median error |
|---|---|---|---|---|
| DA3 504 px, metric on 4 photos/room (**shipped**) | ~18–22 min | 7/7 | 0.9 % | 3.1 % |
| DA3 392 px, metric on 2 photos/room ("fast") | 15 min | 1/7 | 12.1 % | 14.4 % |

The fast setting's metric scale came out ~15 % low (ceilings ~2.4 m vs 2.9 m tape) and Room_2 was lost.
The brief sets no pipeline runtime limit (its 15 minutes is README-to-running on a clean machine), so
accuracy wins; a GPU would make the shipped setting run in a few minutes.
