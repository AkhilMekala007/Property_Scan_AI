# Property Scan AI — technical report

*DRAFT 2026-10-04 05:00 IST. Numbers marked ⏳ are updated after the morning captures (photo retake, repeat room, staged damage, opening ground truth).*

## 1. What it does

One command per capture — `scan run <folder>` — turns an iPhone capture into a dimensioned, stitched floor plan (`plan.png`) and a JSON result against a published schema (`schema/result.schema.json`): rooms with walls, ceiling height, floor area and openings; adjacency; per-surface damage regions with class and metric extent; concealed-damage flags naming the rule that fired; scope line items keyed to surfaces; and a 90 % interval on every number. The tier (photos, video, LiDAR) is detected from the files. Everything runs offline on a CPU laptop (i5-1135G7, 8 GB, no GPU); all pretrained models are listed with licences in every result.

Capture route 2: stock apps and a one-page protocol (`docs/capture_protocol.md`): Stray Scanner for LiDAR, the built-in Camera for video and photos.

## 2. Architecture

```
capture folder ─► detect tier ─► adapter ─► FrameSet (RGB, depth, poses, intrinsics)
   LiDAR: Stray Scanner depth + ARKit poses
   video / photos: DA3-BASE poses + depth, DA3METRIC-LARGE metric scale, gravity from surfaces
FrameSet ─► C3 QC ─► C5 drift correction (LiDAR) ─► C4 SegFormer surface labels ─► C7a voxel fusion + planes
        ─► C6 rooms (floor map) ─► C7b room polygons from wall lines ─► C8 openings (wall elevations)
        ─► C9 stitch (shared walls, overlaps, adjacency) ─► C10 damage (OWL-ViT + mask refinement)
        ─► C11 rules (YAML) ─► C13 contract + intervals (C12 calibrated) ─► result.json, plan.png
```

Design choices worth defending:
- **Structure from planes, not meshes.** Walls, floor and ceiling are fitted planes in a 2 cm labelled voxel grid; room outlines are built from wall lines, so a dimension is a plane-to-plane distance rather than a mesh edge.
- **Measured vs inferred is explicit.** An outline edge with no fitted wall is marked inferred (drawn dashed), widens its interval, and is never searched for openings.
- **One contract for every tier.** Photo and video tiers reuse the LiDAR pipeline unchanged from C3 on; only the source of poses, depth and scale differs, and their error budgets are wider.

## 3. Tier design and device matrix

| Tier | Poses | Depth | Scale | Benchmark (tape) |
|---|---|---|---|---|
| LiDAR (iPhone Pro, Stray Scanner) | ARKit + C5 loop closure | LiDAR | sensor | walls 4/7 in 1 cm/0.5 %, median 0.5 %; ceilings 3/4 in 1.5 cm |
| Video (iPhone 15+) | DA3-BASE, overlapping chunks | DA3 | DA3METRIC per chunk | fails ±3 % (§6) |
| Photos (iPhone 15+, 2–8 per room) | DA3-BASE per room | DA3 | DA3METRIC (2 photos / room) | walls 7/7 in ±8 %, median 0.9 % ⏳ |

Why DA3 for photos and video: COLMAP registered 0 of 8 photos per room (taken turning on the spot: no baseline) and split the video into 18 pieces too short to contain a room; depth-lifted SIFT matching registered 4 of 8. DA3 posed all photos of every room. The focal-blind metric depth model (Depth Anything V2) overestimated scale by ~25 % on the iPhone 15 (ceiling 3.67 m vs 2.93 m); DA3METRIC uses the focal length and gave 2.74 m.

Full matrix: `docs/device_matrix.md` ⏳.

## 4. Drift handling (LiDAR)

Two mechanisms, in order. (1) **Loop closure:** fragments of 20 keyframes that revisit the same space are aligned by multi-scale ICP, accepted only on a clear tight-window fitness gain and a constraint-ratio check (no sliding along one wall), and a yaw + translation pose graph is solved (gravity trusted from the IMU). (2) **Plane-anchored heading** when no revisit qualifies: each fragment's wall directions are compared with the capture's, and the deviation is removed by re-integrating its motion.

On the 3BHK none of 26 revisits overlapped enough (the walk does not return to a surface), so (2) applies: heading drift median 0.42°, max 0.85°; correction brings fragment deviation from 0.51° to 0.10°. Ablation, correction on vs off: wall voxels on planes 0.75 → 0.82, shared walls 3 → 5, footprint 80.9 → 79.3 m² — but tape walls in gate 4/7 → 2/7, because room assembly re-forms outlines after cm-level pose changes (a 0.85° heading error alone changes a 3.5 m wall by < 1 mm). We therefore measure and report drift on every capture and apply the heading correction above 1°; this threshold was chosen after the ablation (`docs/LLD/09_drift.md`). The underlying weakness — outline instability — is the same one that fails repeatability (§8).

## 5. Error budget and calibration

Every number is a `Measure` {value, lo, hi, sigma}: sigma combines the fit uncertainty (plane fits, jamb positions) with per-tier systematic terms (depth bias, labelling, segmentation), z = 1.645 for 90 %. Quantities seen only from one side, or bounded by an unobserved head, are lower bounds.

C12 (`scan calibrate`) fits one factor per tier and quantity on the tape benchmark: k = q90(|error| / sigma) / 1.645, never below 1 (a handful of rows is not evidence for narrower intervals). Values never change, only widths.

| Tier / quantity | n | k | coverage before → after | leave-one-out |
|---|---|---|---|---|
| LiDAR walls | 7 | 1.79 | 71 % → 100 % | 86 % |
| LiDAR ceilings | 4 | 1.09 | 75 % → 100 % | 75 % |
| Photo walls | 7 | 1.00 | 100 % → 100 % | 100 % |
| Photo ceilings | 4 | 2.32 | 75 % → 100 % | 75 % |

In-sample coverage is 100 % by construction; leave-one-out is the honest estimate for a new capture. Video has no matched rows and keeps the provisional budget (marked `calibrated: false`). ⏳ refit after the morning captures.

## 6. The fix loop (worst gate: video wall lengths, ±3 %)

Full record with timestamps: `docs/fix_loop.md`.

**Failing number:** 0 of 10 video wall dimensions matched (9 overlapping partial rooms, 50.9 m² vs 78.8 m²).

**Root cause (revised twice):** the walkthrough is reconstructed in chunks; monocular metric scale varies 13–33 % between chunks. Attempt 1 (16-frame overlap + ICP of each chunk against everything placed): prediction 5 ± 1 rooms, 67–91 m² — got 3 rooms, 31.6 m². Attempt 2 (each chunk measured on its own scale, placed by the chain): prediction ≥ 4 rooms recovered, 55–90 m² — got 57.9 m² but 7 partial rooms (a 30 s chunk sees only part of a room). Attempt 3 (one DA3 pass over the whole video): 1 room, 3.8 m² — 63 m of walking collapsed into 2.3 × 3.4 m; sparse frames break correspondence across similar rooms.

**Why it fell short:** dense chunks keep correspondence but not a shared scale; long passes keep a scale but lose correspondence. The fix this points to is a scale reference shared across the walk (phone odometry, which a plain video file does not carry) or a multi-view model with long-range memory. ⏳ re-recorded slow video.

## 7. Head-to-head (LiDAR vs Polycam)

Polycam (floor-plan export of the same flat) vs our blind LiDAR run, against tape: we beat or tie on **11 of 12** shared dimensions (92 %; tie = within 5 mm). Polycam reports to 0.1 m; its bedroom ceilings read 3.0 m against 2.89–2.94 m. Our one loss is Bedroom 3's width, where our outline follows a wardrobe front (+15.6 cm) — a known failure mode below. Table: `bench/reports/lidar_flat/lidar_flat.md`.

## 8. Known failure modes

- **Furniture against walls** (LiDAR, all tiers): a wardrobe front can be taken as the wall (Room_1 width +15.6 cm).
- **Photo tier stitching:** rooms are placed by matching doors seen from both sides; when doors are not photographed square-on, rooms are reported unconnected (QC warning `fragments_unconnected`). ⏳
- **Photo hall:** photos that look into adjacent spaces enlarge the room (hall 55.8 m² vs 36 m²); the protocol now says to stay inside the room.
- **Video tier:** scale drift between chunks (§6).
- **Mirrors and glass:** mirror-labelled voxels are excluded from structure; glass gives no depth (LiDAR) or plausible-but-wrong depth (DA3) — windows are found from labels plus see-through rays.
- **Low light:** QC flags dark frames (median brightness < 50) and fast motion; blurry frames keep their depth but are excluded from RGB steps.
- **Runtime on CPU:** LiDAR ~6 min cold; photos ~20 min; video ~45 min (DA3 on CPU) — 20–50× faster on a GPU. A faster photo setting (DA3 at 392 px, metric scale from 2 photos per room) was measured and rejected: 15 min, but walls 1/7 in gate (median 12 %) and the metric scale ~15 % low.

## 9. Reproduction

`README.md` ⏳: install, `python scripts/fetch_weights.py`, `scan run`, `scan bench`, `scan calibrate`. Every reported number regenerates from `data/raw/benchmark` with the commands in `bench/README` ⏳. Model outputs are cached per capture and replay deterministically; the live path runs without the cache.
