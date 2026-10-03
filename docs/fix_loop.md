# Fix loop (Part 4) — working declaration

Status: **provisional**. The brief requires the single worst-performing gate *on our own benchmark*. The benchmark captures (with tape ground truth) are not done yet, so this records the leading candidate and its evidence now, while the analysis is fresh. It will be confirmed — or replaced by whichever gate is actually worst — once the benchmark runs.

## 1. Worst gate (candidate) and failing number

**Repeatability:** two captures of the same room at the same tier must agree within 1 cm or 0.5 % per wall.

Proxy measurement available today (`scan repeat`, 3 runs, 5 mm / 0.1° pose noise per fragment, LiDAR tier, sample captures; full table in `docs/LLD/10_repeatability.md`):

| Capture | Room counts | Walls within 1 cm | Wall spread median |
|---|---|---|---|
| `single_scan_floor_only` | 6, 6, 5 | 0 % | 9.3 cm |
| `single_scan_with_ceiling` | 5, 6, 5 | 0 % | 16.3 cm |
| `single_room` | 3, 3, 3 | 25 % | 3.9 cm |

Ceiling height already passes (0.6 cm spread); wall lengths fail badly.

The real failing number will be measured on two captures of the same benchmark room.

## 2. Root-cause hypothesis and evidence

**Hypothesis:** room topology and outlines are built from a pixel floor map (C6 regions, C7b coverage of rectangles). Floor-map cells near walls, doorways and furniture flip under millimetre changes, and room splitting / polygon assembly amplify those flips into missing rooms, merged rooms and outlines that jump by tens of centimetres. The underlying wall planes are stable.

**Evidence:**
- Stage-by-stage comparison of a perturbed and an unperturbed run: after the C7a fix, wall planes move ≤ 13 mm (p90) with 85–88 % within 1 cm, while room areas and counts change — the amplification happens after the walls.
- Debug renders: a corridor's seed (~0.3 m² after erosion) vanished in one run, merging the corridor into its neighbours; a 3 cm sliver between two lines split a room in C7b (fixed); threshold changes in C6 moved the instability between captures instead of removing it.

## 3. Fix and predicted number

**Fix:** build rooms from the global arrangement of fitted wall planes (cells bounded by walls, inside/outside from floor evidence, rooms = cells connected without crossing a wall, doorways = wall gaps). Room boundaries then inherit wall-plane stability.

**Prediction (to be finalised against the real "before" number):** room count identical across repeat captures, and the share of observed walls agreeing within 1 cm rises to roughly the wall-plane level (≈ 85 %) from the measured "before".

## Regenerability

The "before" state will be tagged (`fix-loop-before`) once the benchmark runs; before and after are each regenerated with one command from raw inputs.

---

# Candidate B — video tier, wall lengths (±3 % gate)

Recorded **2026-10-04, before the "after" run finished and before ground truth was available**. Which candidate (A: repeatability, B: video walls) is the worst gate is decided by the benchmark; this section fixes the prediction in advance so it cannot be fitted to the result.

## Before (commit `6ec0cd4`)

DA3-BASE poses in 7 chunks of 32 keyframes, 8 shared frames between consecutive chunks, chunks joined from the shared frames only (orientation → rotation, shared depth → scale, centres → translation).

Proxy numbers against the LiDAR run of the same flat (`bench/runs/blind_v1`, 5 rooms, 78.8 m² net):

| | Video before | LiDAR |
|---|---|---|
| Rooms | 9 (7 overlapping pairs) | 5 |
| Net area | 50.9 m² | 78.8 m² |
| Best bedroom | 3.70 × 3.60 m | 3.72–3.80 × 3.02 m |
| Ceilings | 2.83–3.19 m | 2.90–2.94 m |

## Root cause

The 168 s walkthrough is reconstructed as a **chain** of chunks; each join uses only the 8 frames the chunks share. Errors accumulate along the chain and nothing pulls the chain back when the camera returns to a room, so one wall seen from two chunks lands in two places, which splits rooms (C6) and creates overlaps.

Evidence: the metric scale measured independently in each chunk (DA3METRIC-LARGE) varies 2.44–3.21 m per joint unit along the chain (±13 %); a first version that took the chunk scale from camera centres went negative by chunk 4 (centres move too little across 8 frames).

## Fix (commit `2fea3a4`)

1. Each new chunk is aligned by point-to-plane ICP to the cloud of **everything already placed** (all overlapping surfaces, and earlier rooms when the camera returns), kept only when overlap fitness improves.
2. Chunk overlap 8 → 16 frames (10 chunks).

On the old 8-frame chunks, step 1 alone improved 5 of 6 joins (fitness e.g. 0.07 → 0.25) and gave 5 rooms / 1 overlap, but only 35.9 m² net.

## Prediction (written before the result)

- Room count 5 ± 1, at most 1 overlapping pair.
- Net area within 15 % of the LiDAR 78.8 m² (67–91 m²).
- Bedroom walls within 5 % of LiDAR for the matched rooms; still likely **outside** the ±3 % gate — the remaining error is depth-scale drift within a chunk, which this fix does not address.

## Regenerate

`git checkout 6ec0cd4` (before) / `2fea3a4` (after), then `scan run data/raw/benchmark/video_flat --device "iPhone 15"`. Before uses the cached chunks in `da3_160_32_8_336`; after in `da3_160_32_16_336`.

## Outcome of attempt 1 (recorded 2026-10-04 04:07 IST, after the run)

**Prediction falsified.** After (commit `2fea3a4`, 16-frame overlap + ICP against all placed chunks):
3 rooms, 1 overlapping pair, **31.6 m²** net (predicted 5 ± 1 rooms, 67–91 m²). ICP improved every
accepted join (overlap fitness e.g. 0.14 → 0.31), but the per-chunk metric scale still varies by up to
33 % along the chain. Fusing all chunks into one model smears walls that were seen at different
scales; C6 then drops floor it cannot bound and merges what remains.

Revised root cause: not the joins alone but **fusing depth from chunks whose scales disagree**. Within
one chunk DA3 is self-consistent and the metric scale is measured for that chunk.

## Attempt 2 — measure each chunk on its own (prediction written before the run)

**Fix:** every chunk becomes its own fragment with its own metric scale; each runs the room pipeline
separately (no cross-chunk fusion). Rooms are placed with the chained poses (for layout only); where two
chunks saw the same room, the copy with more observed walls is kept.

**Prediction:** at least 4 of the 5 rooms recovered as separate rooms; matched bedroom walls within
±8 % of the tape (median ≤ 5 %), still mostly outside the ±3 % video gate; net area 55–90 m².

## Outcome of attempt 2 (recorded after the run)

**Prediction largely falsified.** 7 rooms, **9 overlapping pairs**, 57.9 m² net (area inside the predicted
55–90 m², but rooms not recovered): each 32-frame chunk (~30 s of video) sees only part of a room, so
per-chunk rooms are partial (e.g. 1.9 × 4.5 m, 2.5 × 2.0 m) and the duplicate filter cannot merge
partial views of the same room. 7 duplicates removed, 1 chunk gave no room.

| Variant | Rooms | Overlaps | Net m² |
|---|---|---|---|
| Before: 8-frame chain, fused | 9 | 7 | 50.9 |
| Attempt 1: 16-frame chain + ICP, fused | 3 | 1 | 31.6 |
| Attempt 2: per-chunk, placed by chain | 7 | 9 | 57.9 |
| LiDAR reference | 5 | 0 | 78.8 (tape: hall 36 m²) |

Conclusion so far: the video tier is limited by **metric-scale consistency of monocular depth across
the walkthrough** (±13–33 % between chunks); neither tighter joins (attempt 1) nor avoiding cross-chunk
fusion (attempt 2) removes it. A fix needs either a scale-consistent multi-view model over the whole
video (DA3 on all frames at once: memory-bound on this 8 GB CPU laptop) or a sensor scale reference.

## Attempt 3 — the whole video in one DA3 pass (prediction written before the run)

**Fix:** no chunks: DA3-BASE on 48–80 keyframes spread over the whole walkthrough in one pass (lower
resolution to fit 8 GB), so every frame shares one scale; metric scale from DA3METRIC on 4 frames.

**Prediction:** 4–6 rooms, ≤ 2 overlapping pairs, net 60–90 m²; matched bedroom walls within ±8 % of
the tape (median ≤ 5 %); ceilings within ±5 %. Risk: sparse frames (one per 2–3 s) may give rooms with
few observed walls.

## Outcome of attempt 3 (recorded after the run)

**Prediction falsified.** One DA3 pass over 80 keyframes (596 s end to end — fast): **1 room, 3.8 m²**.
Diagnosis: the camera path is 16 DA3 units long (≈ 63 m at the measured scale) but spans only
0.6 × 0.9 units (≈ 2.3 × 3.4 m): with one frame every ~2 s and similar white rooms, DA3 superimposed
different rooms onto one place. Sparse whole-flat sequences break the multi-view model's
correspondence; dense chunks (attempts 1–2) keep correspondence but lose scale consistency.

## Decision (time box reached)

Video tier ships with attempt 2 (per-chunk measurement, 16-frame overlap): the most complete plan
(57.9 m²) and every room measured at a self-consistent scale. Documented as **failing the ±3 % video
gate**; the structural fix is a scale reference shared across the walkthrough (e.g. the phone's own
IMU/ARKit odometry, which a plain video file does not record) or a multi-view model with long-range
memory. The loop's value is the diagnosis: three measured attempts, each prediction recorded before
its result.
