# LLD 04 — C7a Structure (fused model, floor, ceiling and wall planes)

**Goal:** fuse every kept frame into one labelled 3D model and find the floor, every ceiling level and every wall plane of the whole capture. C6 (room split) and C7b (per-room measurements) build on this.

```
FrameSet (QC'd, partly labelled) ──► fuse ──► VoxelGrid (2 cm)
                                              ├─► dominant wall direction
                                              ├─► floor plane
                                              ├─► ceiling levels (or null + reason from QC)
                                              └─► wall planes (one per contiguous segment)
                                         ──► outputs/<capture>/structure.json + structure_debug.png
```

## Modules

| File | Responsibility |
|---|---|
| `scan/structure/voxels.py` | Frame-by-frame fusion into a 2 cm voxel grid with positions, oriented normals, label votes |
| `scan/structure/planes.py` | Dominant direction, wall-direction families, floor / ceiling / wall fits |
| `scan/structure/__init__.py` | `run_structure`, `StructureModel`, JSON writer, top-down debug render |

## Fusion

- Per frame: high-confidence depth, 3×3 median (per-pixel LiDAR noise is ~1 cm at ~1 cm pixel spacing, which makes raw normals noisy), stride 2, world points + normals from neighbouring pixels.
- **Normals are flipped to face the camera**, so every voxel normal points into free space, i.e. into the room. Wall planes therefore know which side the room is on.
- **Labels by voting:** labelled frames add their label confidence to the voxel's vote for that class. Points from unlabelled frames add geometry only and inherit the voxel's winning vote.
- Chunked reduction (40 frames at a time) bounds memory; voxels seen fewer than 3 times are dropped as noise.
- `person` and `mirror` voxels are removed before any fitting.

## Planes

| Element | Candidates | Refinement |
|---|---|---|
| Dominant direction | Vertical wall-labelled voxels | Weighted circular mean of 4·angle (mod 90°) |
| Wall directions | 1° histogram of wall-normal angles (0–360°, sign kept) | Peaks carrying ≥ 3 % of wall voxels; snapped to dominant ±k·90° within 3° |
| Walls | Per direction, 1 cm histogram of plane offsets, peaks ≥ 400 voxels, 6 cm apart | Inliers ±4 cm, split into segments at gaps > 0.4 m, trimmed weighted mean offset per segment |
| Floor | Horizontal floor/unknown voxels within ±0.2 m of QC's floor height | Trimmed weighted least squares `y = ax + bz + c` |
| Ceilings | Horizontal ceiling/unknown voxels > 2 m above the floor | One plane per height peak with ≥ 1 m² area; all levels kept |

Opposite faces of a partition wall are separate planes (opposite normals); C9 will use the pairs for wall thickness.

**Uncertainty at this stage is fit-only** (residual RMS / √n). It excludes LiDAR depth bias and drift, so it is a lower bound; C12 adds the systematic terms from the benchmark.

## Results on the sample captures

| Capture | Voxels | Time* | Directions found | Floor tilt / spread | Ceiling levels (height above floor, area) | Walls |
|---|---|---|---|---|---|---|
| `single_room` | 184 k | 12 s | 22.6 / 112.6 / 202.6 / 292.6° | 0.48° / 8.8 mm | none: QC says not observed | 21 |
| `single_scan_floor_only` | 446 k | 23 s | 86.2 / 176.2 / 266.2 / 356.2° | 0.15° / 10.8 mm | none: QC says not observed | 53 |
| `single_scan_with_ceiling` | 531 k | 24 s | 27.6 / 117.6 / 207.6 / 297.6° | 0.02° / 6.8 mm | 3.078 m (9.5 m²), 2.424 m (8.9), 2.346 m (8.7), 2.963 m (4.9), 2.270 m (3.8) | 58 |

*With QC and labels cached.

- All captures show four wall directions exactly 90° apart: right-angled layouts.
- The five ceiling levels in `with_ceiling` are each flat (3–7 mm spread, < 0.5° tilt) and lie in different parts of the apartment, so they are real per-room ceiling heights (lowered ceilings, bulkheads). C7b must assign ceilings per room.
- Big walls fit with 10–17 mm spread, above the ~5 mm expected from sensor noise alone. Candidate causes: drift between the two passes of the walk (doubled walls), skirting boards and frames labelled wall. To be measured in C5 with the drift ablation.

## Synthetic test room (`tests/test_structure.py`)

A 4.0 × 3.0 m room, 2.6 m ceiling, rotated 20°, 3 mm noise: rotation recovered within 0.5°, ceiling height within 3 mm, opposite-wall spacing within 5 mm, all four walls snapped, a 1 m gap splits a wall into two segments.

## Known limits / next

- Over-segmentation: short fragments (door frames, furniture labelled wall) become small planes; C7b keeps only walls that bound a room.
- Non-right-angled walls are supported as their own direction family but untested on real data.
- No drift correction yet: these are the "drift fix off" numbers for the C5 ablation.
