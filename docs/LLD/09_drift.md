# LLD 09 — C5 Drift correction

**Goal:** stop accumulated ARKit pose drift from distorting a multi-room plan, and prove what the correction does with an on/off ablation. The brief: "Poses used as-is is an automatic fail on this row."

```
QC'd FrameSet (ARKit poses) ──► fragments (20 keyframes) ──► fragment clouds (3 cm)
   ──► odometry edges (consecutive, trusted) + loop closures (ICP between revisits)
   ──► pose graph optimisation (Open3D, line process prunes bad loops)
   ──► yaw + translation correction per fragment ──► corrected keyframes + trajectory
```

## Modules

| File | Responsibility |
|---|---|
| `scan/drift/__init__.py` | `correct_drift`, `DriftConfig`, `DriftReport`, fragment clouds, multi-scale ICP, constraint check, trajectory correction |
| `scan/pipeline.py` | `run_pipeline` (the whole LiDAR chain, drift on by default) and `consistency_metrics` for the ablation |
| `scan/cli.py` | `scan plan --no-drift-fix`, `scan drift <capture>` (ablation) |

## Method

- **Fragments:** consecutive runs of 20 keyframes; a short tail folds into the previous fragment. Each fragment's high-confidence depth (stride 3) is fused into a 3 cm cloud with normals.
- **Odometry edges** between consecutive fragments: identity transform (ARKit trusted locally) with information = point-cloud information + a stiffness floor of 1e5. Without the floor, a fragment with little overlap floated during optimisation (`single_room`: one fragment jumped to 18.4 cm / 2.5° while its neighbours sat at ~9 cm).
- **Loop closures:** fragment pairs ≥ 2 apart whose camera centroids are within 3 m. Multi-scale point-to-plane ICP (6 → 3 → 1.5 cm), projected to yaw + translation after every scale. Accepted only if, at a tight 2 cm window, fitness ≥ 0.30 **and** improves by ≥ 0.02, the correction is ≤ 30 cm / 3°, and the overlap pins horizontal translation in both directions (min/max eigenvalue of the (x, z) information block ≥ 0.05).
- **Optimisation:** Open3D Levenberg–Marquardt global optimisation, reference node 0, edge prune threshold 0.25.
- **Gravity kept exact:** every correction is projected to a rotation about world +y plus a translation, so floors stay level.
- **Apply:** keyframes take their fragment's correction; raw trajectory poses take the correction of the fragment their timestamp falls in.

## What the data taught us

| First attempt | Problem | Fix |
|---|---|---|
| Accept if inlier RMSE ≤ 2 cm at a 6 cm window | RMSE is 20–35 mm even before ICP (sensor noise within the window): nothing accepted | Judge at a tight 2 cm window by fitness and fitness gain |
| Accept any good ICP fit | Overlaps that are one flat wall let ICP slide (13 cm moves with fitness 0.33 → 0.35) | Reject when horizontal translation is unconstrained (eigenvalue ratio) |
| Weak odometry | Fragment spikes | Stiffness floor on odometry edges |

## Ablation (`scan drift <capture>`)

Runs the full pipeline twice (ARKit poses as-is vs corrected) and compares self-consistency, which needs no ground truth: drift makes surfaces seen at different times disagree, so walls fit with more spread, split into near-duplicates, and floors thicken. Outputs `drift_ablation.json`, both floor plans, and `drift_ablation.png` (room outlines overlaid: red off, green on).

`single_scan_with_ceiling` (3.6 min walk, two passes):

| Metric | OFF (ARKit as-is) | ON |
|---|---|---|
| Correction applied | — | 1 loop closure of 46 tested; max 0.8 cm / 0.28° |
| Wall planes | 58 | 58 |
| Wall fit spread, area-weighted | 9.0 mm | 9.03 mm |
| Wall fit spread, big walls (median) | 5.97 mm | 6.89 mm |
| Wall voxels explained | 87.8 % | 88.1 % |
| Floor fit spread | 6.78 mm | 6.82 mm |
| Net area / footprint | 60.78 / 62.40 m² | 61.59 / 62.95 m² |
| Shared walls / overlaps / connected | 2 / 0 / yes | 2 / 0 / yes |

All three sample captures:

| Capture | Correction (loops accepted / tested) | Wall spread area-weighted OFF → ON | Floor spread OFF → ON | Rooms OFF → ON | Net area OFF → ON |
|---|---|---|---|---|---|
| `single_scan_with_ceiling` | 0.8 cm / 0.28° (1 / 46) | 9.00 → 9.03 mm | 6.78 → 6.82 mm | 5 → 5 | 60.78 → 61.59 m² |
| `single_scan_floor_only` | 0.3 cm / 0.26° (1 / 39) | 9.18 → 9.08 mm | 10.81 → 10.70 mm | **6 → 4** | 52.99 → 52.95 m² |
| `single_room` | 1.7 cm / 0.63° (1 / 28) | 6.57 → 7.27 mm | 8.84 → 9.08 mm | 3 → 3 | **23.85 → 22.72 m²** |

**Reading:** on these captures ARKit's own drift is below the sensor noise; the correction moves poses by under a centimetre and leaves consistency unchanged within noise. Observed walls coincide in the overlay; only inferred edges (which follow the rough C6 region extent) shift by a few cm. The correction is kept on by default because it does no harm here and is designed for longer walks; the synthetic test shows it removes real drift when present.

The ablation also exposed **repeatability problems downstream of the poses** — the most important finding of this block:

- A 0.8 cm pose change disconnected the `with_ceiling` plan because a C6 doorway contact measured exactly 0.40 m against a 0.40 m minimum. Fixed: the minimum is now 0.25 m (rooms can only touch through wall gaps).
- **Still open:** on `floor_only` a 0.3 cm pose change turns 6 rooms into 4, and on `single_room` a 1.7 cm change moves net area by 1.1 m² (5 %). C6 seeding (erosion of the floor map) and the C7b inferred edges (taken from the rough region extent) are sensitive to tiny input changes. Sub-centimetre perturbations should not change the room count, so this must be stabilised before the repeatability gate is measured.

## Tests (`tests/test_drift.py`)

Yaw projection removes roll/pitch; constraint ratio flags a single wall; clean synthetic poses are left alone (< 1 cm, < 0.2°); a second lap drifted by 1.5° and 8 cm is pulled back to less than half the error with gravity unchanged; short captures keep their poses; raw trajectory takes the fragment correction.

## Known limits

- **Downstream instability** (C6 room count, C7b inferred edges) under sub-centimetre pose changes — next item to fix.
- Loop closures need revisits; a single pass through a property gives the optimiser nothing to correct.
- Fragments are fixed-size by keyframe count, not by motion.
- Plane-anchored correction across the whole property is not implemented; the ablation shows no remaining drift on the samples that would motivate it.
