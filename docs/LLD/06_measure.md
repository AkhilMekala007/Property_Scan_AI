# LLD 06 — C7b Room measurement

**Goal:** for every room from C6, produce the numbers the gates score: an outline whose corners are wall intersections, each wall's length, floor area, perimeter and ceiling height, each with an uncertainty, and a dimensioned floor plan.

```
StructureModel (C7a walls, floor, ceilings, voxels) + RoomLayout (C6 room regions)
   ──► per room: candidate wall lines ──► line arrangement ──► rectilinear polygon
   ──► wall lengths ± σ, area ± σ, local floor/ceiling fit ──► measurements.json, plan.png
```

## Modules

| File | Responsibility |
|---|---|
| `scan/measure/polygon.py` | `WallLine`, `Edge`; `arrangement_polygon` (lines → room polygon), `fallback_lines`, `polygon_area` |
| `scan/measure/__init__.py` | Wall lines in the room frame, per-room candidate selection, local floor/ceiling, `measure_rooms`, JSON, plan render |

## Why a line arrangement (and what failed first)

The first version traced C6's floor-map outline and snapped each piece to the nearest wall. The outline is ragged (furniture, door notches, cells stopping short of walls), so most pieces found no wall: room_1 of `with_ceiling` came out with 54 edges, mostly unsnapped.

The arrangement method builds the room **from the walls**:

1. **Candidate lines:** fitted walls (axis-aligned in the C6 room frame) whose inward side borders this room for ≥ 0.3 m (absolute; a long plane often spans several rooms). Plus **inferred** lines on straight runs (≥ 0.4 m) of the region boundary that no wall explains (wide openings where C6 split spaces), and fallback lines on region sides with no wall within 0.4 m.
2. Parallel same-facing lines within 3 cm merge (one wall seen as several pieces).
3. The lines cut the plane into rectangles. A rectangle is in the room if ≥ 50 % of it is covered by the room region **grown by 10 cm** (the floor map stops a few cm short of wall faces).
4. The outline of the largest connected set of chosen rectangles is the polygon. Each rectangle is traced as a 3×3 pixel block so contour vertices map exactly onto line coordinates.

Every edge therefore lies exactly on a fitted wall or on an explicitly inferred line; noise smaller than the spacing between real walls cannot create edges. A second bug: requiring 30 % of a wall's length to border the room rejected long planes and let rectangles leak into neighbours (room_1 shrank to 5.5 m²); the absolute 0.3 m rule fixed it (18.3 m²).

## Measurements

| Quantity | Definition | Uncertainty (fit-only) |
|---|---|---|
| Wall length | Corner to corner along the edge (interior, floor level) | √(σ_prev² + σ_next²), the offsets of the two walls that form its corners |
| Coverage | Share of the edge backed by observed wall | — |
| Inferred | No fitted wall, or coverage < 30 % | Inferred lines use σ = 5 cm |
| Floor area | Shoelace on the corners | √Σ (edge length · edge σ)² |
| Ceiling height | Floor and ceiling re-fitted from voxels inside the room (shrunk 0.2 m from its walls), measured at the polygon centroid | √(σ_floor² + σ_ceiling²) |

Fit-only σ excludes LiDAR depth bias and drift; C12 adds them from the benchmark.

## Results on the sample captures

| Capture | Rooms | Total area | Ceilings | Inferred walls |
|---|---|---|---|---|
| `single_room` | 3 (room, corridor, room) | 24.6 m² | n/a (not observed) | 10 |
| `single_scan_floor_only` | 6 incl. corridor | 54.6 m² | n/a (not observed) | 29 |
| `single_scan_with_ceiling` | 5 | 54.3 m² | 2.436 / 3.072 / 2.957 / 2.345 / 2.280 m | — |

`with_ceiling` plan: room_2 is a clean 6-wall room with 83–100 % observed walls (e.g. 4.74 m and 3.15 m sides). Remaining issues are stitching problems for C9: room_1's inferred edge sits on room_3's wall line (no wall thickness between them), and small jogs near the room_5 doorway. `floor_only` has more inferred walls because walls were only seen up to ~1.5 m and the phone never tilted up.

## Tests (`tests/test_measure.py`)

Rectangle (edges exactly on walls), L-shape (6 edges, 12 m²), a bed-sized floor hole and a doorway notch don't change the room, a wardrobe face mid-room doesn't split it, a missing wall becomes one inferred edge with σ = 5 cm, collinear wall pieces merge with correct coverage, and an **end-to-end C7a → C6 → C7b run on a 4.0 × 3.0 m room rotated 20°: walls within 1 cm, area within 0.05 m², ceiling within 5 mm, no inferred walls**.

## Known limits

- Only axis-aligned (right-angled) walls are used; angled walls are counted in `diagnostics.walls_not_axis_aligned`.
- Adjacent rooms are measured independently; shared walls, wall thickness and overlaps are resolved in C9.
- Ground truth is needed to judge real accuracy; the benchmark captures will provide it.
