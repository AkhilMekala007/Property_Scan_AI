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
