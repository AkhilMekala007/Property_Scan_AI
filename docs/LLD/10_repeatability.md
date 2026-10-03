# LLD 10 — Repeatability harness and fixes

**Goal:** the brief's repeatability gate — two captures of the same room at the same tier agree within 1 cm or 0.5 % per wall ("same room in, same plan out"). The drift ablation showed sub-centimetre pose changes changing the room count, so this block measures where noise is amplified and fixes what can be fixed safely.

## Harness: `scan repeat <capture> --runs N`

`scan/repeat.py`. Run 1 uses the capture as-is; runs 2..N apply a small random yaw + translation to every fragment of 20 keyframes (default 5 mm, 0.1°, about what drift correction changes) and to the trajectory, then run the full pipeline with drift correction off (so the perturbation is the only change). Rooms are matched across runs by centroid (0.8 m), observed walls by direction and midpoint, openings by centre. Reported: room / opening counts, room area spread, wall length spread (median, p90, share within 1 cm), ceiling spread, opening width spread. Written to `outputs/<capture>/repeatability.json`.

This emulates only the pose part of a second capture (not new sensor noise or a different walk), so it is a lower bound on the real instability: anything that moves here will move more between two real captures.

## Baseline (before this block), `single_scan_floor_only`, 3 runs

| Room counts | Room area spread (max) | Wall length spread median / p90 | Walls within 1 cm | Opening width spread (median) |
|---|---|---|---|---|
| 6, 6, 5 | 49 % | 2.5 cm / 37 cm | 22 % | 1.1 cm |

## Where noise is amplified (stage-by-stage, perturbed vs unperturbed run)

| Stage | Behaviour under 5 mm pose noise |
|---|---|
| C7a wall planes | median shift ≈ input (4.9 mm) but p90 34.6 mm: some planes jump |
| C6 room regions | areas stable within ~1 % on one seed; on another a corridor's seed vanished and the room count changed |
| C7b room polygons | the largest room's area jumped 24.6 → 17.1 m² while its C6 region stayed at ~22 m² |

## Fixes kept

| Stage | Root cause (found with debug renders) | Fix | Effect |
|---|---|---|---|
| C7a | A wall and a skirting board / frame / wardrobe front a few cm in front of it give two similar histogram peaks; which one won flipped | `_deepest_peak`: among strong maxima (≥ 40 % of the peak) up to 8 cm deeper, take the deepest — objects stand in front of walls, never behind | plane shift p90 34.6 → 10–13 mm; 85–88 % of walls within 1 cm |
| C7b | A sliver between two lines closer than one 5 cm grid cell had zero pixel area, so its coverage read 0 and it split the room; the smaller piece was dropped | slivers are measured over at least one cell | largest room 24.4 vs 10.8 m² → 24.4 vs 24.0 m² on the same seed |
| C7b | One coverage threshold: borderline rectangles flipped | hysteresis (strong 0.6, weak 0.4 if connected to strong) | |
| C7b | "Largest piece" by rectangle area preferred rectangles half outside the room (27.5 m² polygon for a 21.3 m² region) | choose the piece covering the most real room area; bounding-box fallback lines only when a direction has < 2 lines | |
| C7b | Outline lines skipped within 25 cm of a wall left big half-covered rectangles | outline lines along every straight boundary run (skip only within 4 cm of a same-facing line) | |

## Current state (C7a + C7b fixes kept, C6 unchanged), 3 runs, 5 mm / 0.1°

| Capture | Room counts | Room area spread (max) | Wall spread median / p90 | Walls within 1 cm | Ceiling spread (max) | Opening width spread (median) |
|---|---|---|---|---|---|---|
| `single_scan_floor_only` | 6, 6, 5 | 45 % | 9.3 / 28.5 cm | 0 % | n/a | n/a (too few matched) |
| `single_scan_with_ceiling` | 5, 6, 5 | 12 % | 16.3 / 31.1 cm | 0 % | **0.6 cm** | 28 cm |
| `single_room` | 3, 3, 3 | 4.5 % | 3.9 / 11.1 cm | 25 % | n/a | 1.6 cm |

Ceiling heights are already repeatable (0.6 cm spread, inside the 1 cm gate): they come from large plane fits, not from room outlines. Wall lengths are not: they are corner-to-corner along room polygons, so they inherit every change in room topology even though the wall planes themselves are stable (85–88 % within 1 cm). This is the "before" for the fix-loop candidate.

## Tried and reverted

Lowering the C6 seed minimum (0.2 → 0.05 m²), merging regions across wide doorless contacts, and closing single-cell barrier gaps stabilised the room count on `floor_only` (6, 6, 5 → 7, 7, 7) but destabilised `with_ceiling` (5, 7, 5). Moving thresholds moved the instability rather than removing it, so the change was reverted.

## Remaining instability and planned fix (fix-loop candidate)

Room-level topology — how the floor is split into rooms (C6) and how each outline is assembled from pixel regions (C7b) — still flickers under millimetre noise. Root cause: rooms are built from a **pixel floor map**, whose cells near walls, doorways and furniture flip with tiny changes, and every decision on top inherits that. The wall planes underneath are now stable.

**Planned fix:** build rooms from the **global wall arrangement** — all fitted walls of the property cut the floor into cells, cells are classified inside/outside from floor evidence, rooms are groups of inside cells connected without crossing a wall, doorways are wall gaps between them. Every room boundary is then a stable wall plane. This is recorded in `docs/fix_loop.md` with the prediction, to be confirmed or replaced by the worst gate on the real benchmark.

## Tests (`tests/test_repeatability.py`)

Deepest strong peak wins over a stronger skirting board; a weak deeper bump is ignored; a dense skirting board 3 cm in front of a wall leaves the wall offset within 6 mm; a 3 cm sliver doesn't split an 18 m² room; a thin protrusion doesn't inflate a room; a 1 m corridor keeps its seed; perturbation is deterministic, small and keeps gravity; identical runs report zero spread.
