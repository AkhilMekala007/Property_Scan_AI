# LLD 08 — C9 Stitching

**Goal:** turn separately measured rooms into one consistent whole-property plan — the product surface: no overlaps, shared walls with real thickness, one opening per physical door/window, a connected adjacency graph, a footprint, and a rendered plan a homeowner would recognise.

```
RoomMeasurement (C7b) + Opening per room view (C8) + Doorways (C6)
   ──► shared walls ──► resolve overlaps ──► merge opening views ──► adjacency + connectivity
   ──► footprint ──► plan.json + floor_plan.png
```

LiDAR and video captures share one world frame, so this is consistency work. Photo-tier stitching (placing rooms by matching doors) comes with the photo adapter.

## Modules

| File | Responsibility |
|---|---|
| `scan/stitch/__init__.py` | `stitch`, `PropertyPlan`, `SharedWall`, `PlanOpening`, overlap resolution, opening merge, JSON |
| `scan/stitch/render.py` | `render_floor_plan`: walls at measured thickness, door swings, windows, dimensions, labels, scale bar |
| `scan/measure/__init__.py` | Candidate-wall rule tightened (see below) |

## Steps

1. **Shared walls:** pairs of observed edges from two rooms, same axis, facing away from each other, 3–40 cm apart, running alongside for ≥ 0.3 m. The gap is the wall thickness.
2. **Overlaps:** rooms are rasterised at 2 cm; for each overlapping pair, an **inferred** edge lying inside the neighbour is pulled back to the neighbour's facing edge plus the median wall thickness (0 if that edge is also inferred). Moves are capped at 1 m: a bigger move means a whole edge would be shifted for a local intrusion, discarding real area, so the overlap is reported instead. **Observed walls are never moved.** Remaining overlaps are reported, and `scan plan` exits 1.
3. **Openings:** views from different rooms within 0.5 m (same window/non-window class) are one opening. If widths agree within 3σ + 2 cm, they are combined by inverse variance. If not, the **narrower** view is taken — the clear opening is the narrowest section through the wall (door stops and frames make one face's gap wider) — with σ ≥ half the disagreement. Covered and low-evidence views are used only when nothing better exists.
4. **Adjacency:** rooms joined by merged openings; C6 doorways with no opening nearby are added as `doorway` and listed in `unmeasured_doorways` (each is a likely missed opening). Connectivity by union-find.
5. **Footprint:** union of room polygons with wall gaps bridged (closing by the maximum wall thickness) = interior footprint. Net area = sum of room areas.

## A C7b bug found here

The first stitch found **0 shared walls**: room_1 and room_2 both used the **same** fitted wall face (plane #4) from opposite sides. A wall face points into one room only; the C6 region of room_1 leaked 10–30 cm past the partition, so the C7b candidate test (room on the inward side?) passed for room_1 too. Rule added: a wall is a candidate only if the room lies **mostly** on its inward side (outward hits ≤ 50 % of inward hits). Result on `with_ceiling`: 2 shared walls, median thickness 8.2 cm, overlaps gone.

Consequence for openings: room_1 now measures the room_1–room_2 door on its own face (0.902 m), room_2 on the other face (0.802 m). Before the fix they "agreed" (0.806 / 0.802) only because both used the same face. The narrowest-section rule picks 0.802 m.

## Results on the sample captures

| Capture | Rooms | Net / footprint | Shared walls (thickness) | Openings (two-sided) | Connected | Overlaps | Unmeasured doorways |
|---|---|---|---|---|---|---|---|
| `single_scan_with_ceiling` | 5 | 60.8 / 62.4 m² | 2 (8.2 cm) | 12 (1) | yes | none | 1 |
| `single_scan_floor_only` | 6 | 53.0 / 54.8 m² | 1 (9.7 cm) | 7 (0) | yes | 1 × 0.09 m² (observed walls) | 3 |
| `single_room` | 3 | 23.9 / 24.4 m² | 0 | 3 (0) | yes | none | 1 |

Unmeasured doorways in `floor_only` reflect the low-evidence capture (walls never seen above ~1.6 m).

## Tests (`tests/test_stitch.py`)

Shared wall of 0.10 m found and bridged in the footprint; an intruding inferred edge pulled back to exactly 5.0 m; observed walls never moved and their overlap reported; agreeing views averaged (0.800 + 0.804 → 0.802); disagreeing views take the clear opening (0.902 / 0.802 → 0.802, σ 5 cm); distant windows stay separate; adjacency with an unmeasured doorway flagged; disconnected rooms reported.

## Known limits

- Inferred edges are moved whole; a partial intrusion of a long edge is handled only up to the 1 m cap.
- Exterior wall thickness is not observed (only interior faces are seen), so the footprint is the interior footprint.
- The capture protocol must define door width as the clear opening (narrowest point between the frames) so ground truth matches.
