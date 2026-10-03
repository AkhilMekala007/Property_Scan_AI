# LLD 07 — C8 Openings

**Goal:** find doors, windows and open passages in every room wall and measure their width and height. The opening gate is the hardest: width ≤ 2 cm on ≥ 85 % of openings, with missed and phantom openings both counted as failures.

```
RoomMeasurement edges (C7b) + voxels (C7a) + posed depth frames
   ──► per wall: elevation image (along-wall × height, 2 cm)
   ──► opening candidates ──► classify ──► width from ray crossings / jambs, head, sill
   ──► link doors to C6 doorways ──► openings.json, plan_openings.png
```

## Modules

| File | Responsibility |
|---|---|
| `scan/openings/elevation.py` | `WallSpec`, `Elevation` (solid / through / door / window layers + exact samples), `build_elevation`, debug render |
| `scan/openings/__init__.py` | Components, classification, ray-edge and jamb measurement, `find_openings`, JSON |
| `scan/measure/__init__.py` | `render_plan(..., openings=)` draws doors / windows / passages |

## Evidence layers

| Layer | Source |
|---|---|
| solid | Fused voxels on the wall plane (±5 cm), vertical, not door/window/person/mirror |
| **through** | **Camera rays** from frames inside the room whose depth hit lies > 8 cm beyond the plane; the crossing point is stored exactly |
| door / window | Door- / window-labelled voxels from −10 cm to +25 cm of the plane (curtains hang in front) |

**Why rays, not voxels, for "through":** the fused model also contains the neighbouring room (the camera walked there), which lies behind every partition wall and would make whole walls look open. A ray only crosses the plane where there really is a gap.

## Detection and classification

- Candidates: cells with evidence (≥ 2 rays, or labels) and no solid wall; closed by 6 cm; pieces stacked in the same columns with ≤ 30 cm vertical gap merge (window frame bars, transoms).
- Cells with **no evidence at all** are never openings: furniture hiding a wall is not a gap.
- Edges with no fitted wall (C7b inferred lines) are skipped; low-coverage walls are kept, because a window wall is mostly glass and is rarely "seen".

| Kind | Rule |
|---|---|
| door | Reaches the floor (≤ 15 cm), tall (≥ 1.6 m, or runs up to the highest observed part of the wall), seen through by ≥ 200 rays with door labels; width ≤ 1.5 m |
| opening | Same, but no door labels, or wider than 1.5 m |
| closed door (`covered`) | Door labels only, door-sized (0.6–1.3 m wide, top ≥ 1.8 m or unseen); otherwise rejected as wardrobe/cabinet doors |
| window | Starts ≥ 0.25 m above the floor with rays or window labels; floor-length curtains with labels only are covered windows |

## Measurement

- **Width of a seen-through opening = where rays passed through.** Per 2 cm height row, the 3rd-outermost ray crossing on each side; median over rows. A wall jamb found within 4 cm of that edge refines it (surface edge + half a voxel). σ = row MAD / √rows per side.
- Closed doors and curtains use jambs or the label extent; curtain-covered windows get σ = 5 cm per side (the window behind is hidden).
- Head: 5th percentile height of the wall above the opening; if none was observed, `head_observed = false` and the reported top is a lower bound. Sill: 95th percentile of the wall below a window.
- `low_evidence`: fewer than 5000 rays through an open gap; σ is raised to at least 5 cm because sparse rays fall short of the jambs.
- Doors are linked to the nearest C6 doorway within 0.7 m to record which room they lead to.

## What the sample data changed

| Problem seen | Fix |
|---|---|
| A 5.47 m "door" on an inferred room edge | Skip edges with no fitted wall; gaps wider than 1.5 m are open passages |
| One window reported as three (frame bars) | Merge vertically stacked pieces |
| Door width included the open door leaf beside the clear gap | Width from ray crossings, jambs only when they agree within 4 cm |
| `floor_only`: 0 doors although C6 found 5 doorways (walls never seen above ~1.6 m) | Doors may run up to the observed wall top; head reported as unseen |
| 2nd/98th percentile edges biased inward by 2 % of the width (0.80 → 0.765 m, 2.40 → 2.29 m in tests) | 3rd-outermost crossing per row |

After the last fix the door between room_1 and room_2 of `single_scan_with_ceiling` measures **0.806 m from one side and 0.802 m from the other** (two independent walls, 4 mm apart; it was 9 cm apart with the percentile estimator).

## Results on the sample captures

| Capture | Doors | Windows | Passages | Notes |
|---|---|---|---|---|
| `single_scan_with_ceiling` | 6 | 5 | 1 | Interior doors 0.51–0.94 m, door heads 2.14 m where observed; 1 closed door and 1 curtain window covered; 1 low-evidence door |
| `single_scan_floor_only` | 4 | 2 | 1 | Heads mostly unseen (phone never tilted up); 3 doors low-evidence |
| `single_room` | 2 | 1 | 0 | |

## Tests (`tests/test_openings.py`)

Synthetic elevations: open door 0.80 m (width within 2 cm, head within 3 cm), window with sill, window split by a transom, 2.4 m open passage, closed door from labels, wardrobe-sized labels rejected, furniture occlusion not an opening, unseen head, low-evidence flag.

## Planned improvement (fix loop)

Depth-only widths are limited by 2 cm voxels and ray density. The planned fix-loop step is **RGB edge refinement**: project the 1920×1440 images onto each wall plane (≈ 1–2 mm per pixel at 2 m) and locate jamb edges in the image, combining many views. Benchmark before/after on tape-measured openings.

## Known limits

- One physical door is reported once per room that sees it; C9 merges them.
- Windows behind curtains have hidden extents (covered, σ 5 cm).
- Only axis-aligned walls are searched.
