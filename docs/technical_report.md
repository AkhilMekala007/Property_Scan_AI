# Property Scan AI — technical report

## 1. What it does

`scan run <folder>` turns an iPhone capture into a dimensioned floor plan (`plan.png`) and a JSON result validated against a published schema (`schema/result.schema.json`). The result has:

- rooms with walls, ceiling height, floor area and openings
- adjacency between rooms
- per-surface damage regions with class and metric extent
- concealed-damage flags naming the rule that fired
- scope line items keyed to surfaces
- a 90 % interval on every number

The tier (photos, video or LiDAR) is detected from the files. Everything runs offline on a CPU laptop (i5-1135G7, 8 GB, no GPU). Every result lists the models it used, with their licences.

**Capture route 2:** stock apps and a one-page protocol (`docs/capture_protocol.md`). Stray Scanner records the LiDAR tier; the built-in Camera records video and photos (1× lens, landscape).

## 2. Architecture

```
folder ─► detect tier ─► FrameSet (RGB, depth, poses, intrinsics)
  LiDAR: Stray Scanner depth + ARKit poses ─► C5 drift check / correction
  photos: per room folder ─► DA3-BASE poses + depth, DA3METRIC scale ─► one fragment per room
  video: split into rooms by shared views ─► each room like a photo folder
FrameSet ─► C3 QC ─► C4 SegFormer labels ─► C7a labelled voxels + planes ─► C6 rooms ─► C7b polygons
  ─► C8 openings ─► fragments placed by matching doors ─► C9 stitch ─► C10 damage ─► C11 rules
  ─► C13 contract with C12-calibrated intervals ─► result.json, plan.png
```

These design choices are defended in `docs/HLD.md` and `docs/LLD/`:

- **Structure from planes.** Walls, floor and ceiling are planes fitted to a 2 cm labelled voxel grid, and room outlines are built from wall lines.
- **Measured and inferred are explicit.** An outline edge with no fitted wall is drawn dashed and gets a wider interval.
- **One pipeline for every tier.** Only the source of poses, depth and scale differs between tiers.
- **Refusing to guess.** Rooms that cannot be placed through a door are reported as unconnected (`fragments_unconnected`), not placed by assumption.

## 3. Tiers, device matrix and benchmark

The benchmark is a 3BHK flat: 3 bedrooms, a kitchen, and a hall that doubles as the connector. Each room was measured with a tape, and the same flat was captured at all three tiers. The full tables are in `bench/REPORT.md`, generated from data by `scripts/make_bench_report.py`.

| Tier (device) | Poses / scale | Walls in gate | Median wall error | Ceilings in gate |
|---|---|---|---|---|
| LiDAR (iPhone 17 Pro) | ARKit + drift check / sensor | 4/7 (1 cm or 0.5 %†) | **0.5 %** | 3/4 (≤ 1.5 cm) |
| Photos (iPhone 15, 6–8 per room) | DA3 per room / DA3METRIC | **7/7** (±8 %) | 0.9 % | 3/4 |
| Video (iPhone 15) | DA3 per room segment / DA3METRIC | 0/10 (±3 %) | — | 0/5 |

† The brief gives no LiDAR wall gate, so the repeatability tolerance is used as a stand-in.

**Why DA3 for photos and video.** COLMAP registered no photos per room: the photos are taken while turning on the spot, so there is no baseline. On video, COLMAP split the walkthrough into 18 pieces, each too short to contain a room. DA3 posed every photo in every room.

**Choosing the scale model.** The focal-blind metric model (Depth Anything V2) overestimated scale by about 25 % (ceiling 3.67 m against 2.93 m). DA3METRIC uses the focal length and gave 2.74 m.

**Capture lessons, all measured** (`docs/LLD/13_video_photo_tiers.md`):

- **Ultra-wide 0.5× lens:** DA3 misjudges its focal length by 18–43 %, and rooms came out up to 11 % small.
- **Close-ups along the walls:** too little floor is visible, so no room is built.
- **Portrait orientation:** the metric scale becomes unstable (30 % error in photos, a 2.7× spread across video segments).

These findings drove the protocol: 1× lens, landscape, shoot across the room from the corners.

## 4. Drift handling (LiDAR)

There are two mechanisms, tried in order.

1. **Loop closure.** Fragments of 20 keyframes that revisit the same space are aligned by multi-scale ICP. An alignment is accepted only if it clearly improves the tight-window fitness and passes a constraint check that rules out sliding along a wall. A yaw-and-translation pose graph is then solved.
2. **Plane-anchored heading.** When no revisit qualifies, each fragment's wall directions are compared with the capture's dominant directions.

On the 3BHK, none of the 26 revisits overlapped enough, because the walk never returns to a surface. Measured heading drift was 0.42° median and 0.85° maximum. The ablation (correction on versus off):

| Metric | Correction off | Correction on |
|---|---|---|
| Wall voxels on planes | 0.75 | 0.82 |
| Shared walls | 3 | 5 |
| Footprint | 80.9 m² | 79.3 m² |
| Tape walls in gate | 4/7 | 2/7 |

A 0.85° heading error changes a 3.5 m wall by less than 1 mm. The loss comes from room assembly re-forming outlines after centimetre-level pose changes, so drift is measured and reported on every capture, and the correction is applied only above 1°. That threshold was chosen after this ablation (`docs/LLD/09_drift.md`).

## 5. Error budget and calibration

Every number is a `Measure` {value, lo, hi, sigma}. Sigma combines the fit uncertainty (plane fits, jamb positions) with per-tier systematic terms, and z = 1.645 gives the 90 % interval. Lower bounds are used where the head of an opening was never seen.

C12 (`scan calibrate`) fits one factor per tier and quantity on the tape benchmark: k = q90(|error| / sigma) / 1.645, never below 1. Values never change, only interval widths.

| Tier / quantity | n | k | Coverage before → after | Leave-one-out |
|---|---|---|---|---|
| LiDAR walls | 7 | 1.79 | 71 % → 100 % | 86 % |
| LiDAR ceilings | 4 | 1.09 | 75 % → 100 % | 75 % |
| Photo walls | 7 | 1.00 | 100 % → 100 % | 100 % |
| Photo ceilings | 4 | 1.72 | 75 % → 100 % | 75 % |

In-sample coverage is 100 % by construction, so leave-one-out is the honest estimate (75–100 %). With n = 4–7 rows per factor, the factors are uncertain, and the report says so. Video has no matched rows, so it keeps the provisional budget and stays marked `calibrated: false`.

**Wider intervals for thinner data.** Photo intervals are about ±0.3 m, against about ±0.03–0.09 m for LiDAR.

## 6. Fix loop: video wall lengths (±3 %)

The one-page declaration is in `docs/fix_loop_declaration.md`; the full timestamped log is in `docs/fix_loop.md`.

**Worst gate:** 0/10 video dimensions matched. Every prediction below was written before its result.

| Attempt | Predicted | Got |
|---|---|---|
| Stronger chunk joins (ICP) | 5 ± 1 rooms, 67–91 m² | 3 rooms, 31.6 m² |
| Each chunk measured on its own | ≥ 4 rooms, 55–90 m² | 7 partial rooms, 57.9 m² |
| One DA3 pass | 4–6 rooms | 1 room, 3.8 m² |
| Room mode (shipped) | 3–6 partial rooms, gate stays failing | 5 partial rooms, overlaps 7 → 0, ceilings ~18 % low |

Video ceilings came out ~18 % low on two different clips (2.32–2.45 m vs 2.89–2.94 m tape): a systematic scale bias that a per-tier scale factor could remove, but fitting it on two captures would be overfitting, so it is reported, not applied. The **final diagnosis** is that monocular metric scale is consistent only within one set of views, and that set must show the whole room. The shipped room mode applies the photo-tier method to video segments and is about 3× faster. The gate did not pass.

The structural fix is a scale reference shared across the whole walk, such as phone odometry, which a plain video file does not carry.

## 7. Head-to-head (LiDAR versus Polycam)

Polycam's floor-plan export of the same flat was compared with our blind LiDAR run, against tape. We beat or tie on **11 of 12** shared dimensions (92 %), where a tie means within 5 mm.

- Polycam reports dimensions only to 0.1 m.
- Its bedroom ceilings read 3.0 m against 2.89–2.94 m.
- Our one loss is Room_1's width, where our outline follows a wardrobe front (+15.6 cm).

## 8. Repeatability, damage, known failure modes

- **Repeatability: not repeatable.** Room_3 was captured twice at the photo tier. Width agreed within 2.6 cm, but length (3.92 m against 4.48 m) and ceiling (2.74 m against 3.15 m) moved with the metric scale, by about 8–15 %. That is within the ±8 % intervals but fails the 1 cm gate. At the LiDAR tier, a pose-perturbation proxy shows outline instability; this is the same weakness as in §4.
- **Damage: not met.** One class was staged (a coffee stain, 24 × 18 cm, on paper) and it was not detected. A stain on a taped sheet resembles the decoy prompts ("a poster") that suppress false positives. The second class was not staged.
- **Photo stitching: fails.** Rooms are placed by matching a door seen from both sides, and the benchmark photos never show a doorway fully from both sides. The rooms are measured and reported unconnected.
- **Furniture against walls:** a wardrobe front can be taken as the wall.
- **Stepped ceilings:** the photo tier picks the higher central level (hall 2.72 m), while the tape and LiDAR read the lowered border (2.50 m and 2.48 m).
- **Mirrors and glass:** mirror-labelled voxels are excluded from structure. Glass gives no LiDAR depth and plausible-but-wrong DA3 depth, so windows are detected from labels plus rays that pass through.
- **Low light:** QC flags dark frames and fast motion. Blurry frames keep their depth but are excluded from RGB steps.
- **Runtime on CPU:** LiDAR about 6 min, photos 22–27 min, video about 25 min. A tested faster photo setting was rejected because walls in gate fell from 7/7 to 1/7. A GPU makes the photo and video tiers take a few minutes.

## 9. Reproduction

`README.md` covers install, the DA3 pinned install, `scripts/fetch_weights.py`, then `scan run`, `scan bench`, `scan calibrate` and `scripts/make_bench_report.py`. The blind LiDAR run is frozen in `bench/runs/blind_v1`.

DA3 outputs are cached per capture, and the live path returns exactly what the cache replays (float16), so cold and cached runs agree.
