# LLD 05 — C6 Room segmentation

**Goal:** split one continuous capture into rooms (including corridors, the brief's "connector") and find the doorways between them. C7b measures each room precisely; C9 assembles the stitched plan.

```
StructureModel (C7a) + trajectory ──► floor map (5 cm, room-aligned)
                                   ──► seeds (cut at doors and narrow necks) ──► grow back
                                   ──► rooms + doorways + per-room ceiling ──► rooms.json, rooms_debug.png
```

## Modules

| File | Responsibility |
|---|---|
| `scan/rooms/floormap.py` | `GridFrame` (room-aligned 2D grid), `build_floor_map`: inside / barrier / door / ceiling-height layers |
| `scan/rooms/segment.py` | Seeding, geodesic growth, small-room merge, contacts (doorways), corridor test |
| `scan/rooms/__init__.py` | `segment_rooms`, `Room`, `Doorway`, `RoomLayout`, JSON + render |

## Floor map

Grid rotated by the dominant wall direction, so walls run along the axes.

| Layer | Evidence |
|---|---|
| **inside** | floor voxels; furniture tops (beds, tables: `other`, horizontal, 0.2–1.5 m); cells with ceiling overhead; the walked path (0.25 m radius). Closed by 10 cm, minus barriers, then holes that can't reach the border without crossing a wall are filled |
| **barrier** | wall/window voxels at body height (0.3–1.9 m above the floor), plus fitted C7a wall segments **only within 15 cm of body-height evidence** |
| **door** | door-labelled voxels at 0.3–2.0 m |
| **ceiling_y** | median height of ceiling voxels per cell |

**Why fitted walls are only drawn near evidence:** C7a wall planes include the lintel above each door, so whole segments run straight across doorways. Drawing them sealed every doorway (first run: 5 rooms, 0 doorways). Drawing only near body-height evidence seals gaps up to 30 cm (furniture hiding a wall) and leaves doorways open.

## Segmentation

1. **Seeds:** inside cells minus a 15 cm band around door voxels, eroded by 0.30 m. Openings narrower than 0.6 m (or framed by a door) break; corridors (~1 m wide) survive as their own seed. Components ≥ 0.2 m² become seeds, numbered largest first.
2. **Grow:** multi-source BFS assigns every inside cell to its geodesically nearest seed.
3. **Merge** regions under 0.8 m² into the neighbour they share most boundary with.
4. **Drop unreached areas:** a region with no doorway and no walked path was only seen from outside (through a window or door); it is removed and reported in `unreached_areas_m2`.
5. **Doorways:** connected groups of cells where two rooms touch directly; width = longest extent; `door` if door voxels lie within 15 cm, else `opening`. Widths are rough (frames and open leaves narrow them); C8 measures openings precisely.
6. **Per room:** outline (simplified contour), rough area, walls whose inward normal points into the room, ceiling level by majority vote of the room's ceiling cells (within 5 cm of a C7a level), height at the room centroid. No ceiling → `null` with a reason.
7. **Corridor:** ≥ 2 doorways and a minimum-area rectangle with aspect ≥ 2.5.

## Results on the sample captures

| Capture | Rooms | Doorways | Total area* | Ceilings | Notes |
|---|---|---|---|---|---|
| `single_room` | 3 (room, corridor, room) | 2 doors | 19.5 m² | n/a (QC: not seen) | The "single room" capture also walks into a corridor and a second room |
| `single_scan_floor_only` | 6 incl. 1 corridor linking 3 rooms | 4 doors, 1 opening | 47.1 m² | n/a (QC: not seen) | 1 area (1.6 m²) seen only from outside, dropped |
| `single_scan_with_ceiling` | 5 | 4 doors | 55.5 m² | 2.43, 3.08, 2.96, 2.34, 2.27 m, one per room | Each room matched to its own C7a ceiling level |

*Rough floor-map areas; C7b computes areas from wall planes.

Adding furniture tops and overhead ceiling as inside evidence raised `with_ceiling` from 43 m² to 55.5 m²: floor hidden under beds and cabinets had been left out.

The two apartment scans do not look like the same layout in the debug images, so their room counts are not expected to match.

## Tests (`tests/test_rooms.py`)

Synthetic floor maps drawn in metres: two rooms split at a framed door (doorway 0.8 m ± 0.15), a 0.5 m opening without a frame still splits, a 2 m opening stays one open-plan room, a 1 m × 5 m corridor between two rooms becomes its own corridor room with 2 doorways, rooms behind a solid wall don't touch, small fragments merge, unreached areas are dropped, rotated grid round trip.

## Known limits

- Open-plan spaces joined by openings wider than ~0.6 m without door frames are one room (by design); a homeowner might name them separately.
- Doorway widths here are markers only.
- Thresholds (0.30 m neck, 0.8 m² minimum room) are tuned on three captures; the benchmark captures will test them.
