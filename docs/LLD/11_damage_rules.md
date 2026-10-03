# LLD 11 — C10 Damage and C11 Rules

**Goal:** per-surface damage regions with class and metric extent, concealed-damage flags with the rule that fired, and scope line items keyed to surfaces (brief, Part 2).

```
labelled keyframes ──► OWL-ViT (damage + decoy prompts, cached) ──► filters (threshold, decoys, size)
   ──► refine box to off-colour surface pixels ──► reject corner lines ──► back-project with depth
   ──► assign to a room surface ──► area on a 1 cm grid ──► fuse views ──► DamageRegion
DamageRegion + rooms ──► rules/damage_rules.yaml ──► Flag (rule id) + ScopeItem (qty ± σ, surface)
```

## Modules

| File | Responsibility |
|---|---|
| `scan/damage/detect.py` | Prompts, `OwlDetector`, upright → sensor box mapping, `filter_detections`, per-frame cache |
| `scan/damage/measure.py` | `refine_mask` (Lab anomaly + corner-line rejection), `project_mask` (surface assignment), `footprint` (area, σ, outline) |
| `scan/damage/__init__.py` | `detect_damage`, multi-view `fuse`, `DamageRegion` |
| `scan/rules/__init__.py` | YAML loader, matcher, safe quantity formulas with σ propagation, `Flag`, `ScopeItem` |
| `rules/damage_rules.yaml` | 8 rules: ceiling / low wall / high wall / floor water stains, long / ceiling / short cracks, mold |

## Model choice (measured on this CPU)

| Option | Speed | Finding |
|---|---|---|
| OWLv2 base (HLD plan) | 11–13 s / image | ~20 min for 100 frames; on clean walls it scored "water stain on a wall" 0.2–0.31 repeatedly |
| **OWL-ViT B/32 (used)** | **1.4–1.8 s / image** | On clean sample walls the highest damage score in 20 frames was 0.14 (median 0.08); doors and light switches recognised |
| SAM 2-tiny masks (HLD plan) | ~1–2 s / mask, needs torchvision | Replaced by a classical refinement (below); SAM 2 stays the documented upgrade if staged-damage masks are poor |

**Detection:** 3 damage classes (water_stain, crack, mold), each with several phrasings (best score wins), plus 12 decoy prompts (poster, painting, light switch, outlet, shadow, door, window, lamp, curtain, cable, shelf, picture frame). Up to 40 sharp labelled keyframes; results cached per frame.

**Filters:** score ≥ 0.18 (clean-wall maximum was 0.14); dropped if ≥ 60 % inside a stronger decoy box; dropped if the box covers > 25 % of the image; one class per location.

**Refinement (replaces SAM 2):** inside the box, wall/floor/ceiling pixels whose Lab colour differs from the surface in a ring around the box by more than max(10, median + 3·MAD) ΔE; open/close. A mask that lies mostly within 12 px of a boundary between differently labelled surfaces is a corner line, not damage.

**Measurement:** mask pixels (stride 2, full resolution) take depth from the nearest depth pixel, are back-projected and assigned to the room surface (C7b wall planes, room floor, room ceiling) that ≥ 60 % of them lie on (± 6 cm). Area is the filled footprint on a 1 cm grid, with gaps up to the point spacing closed; σ = half the difference between ±1 cm dilated and eroded footprints.

**Fusion:** views on the same surface within 0.3 m merge; class by score-weighted vote; area = median of agreeing views; σ = max(median view σ, spread across views). A region needs ≥ 2 views unless one view scores ≥ 0.45.

## What the sample data changed

On the clean sample captures the first version reported a "crack" on a ceiling and fired a structural-engineer flag. All six detections above threshold were straight corner lines (wall/ceiling, wall/wall) or a cabinet edge, in boxes covering 20–40 % of the image. Added: the box-size prior, the corner-line test, and the stricter single-view rule. Result on all three captures: **0 regions, 0 flags** (2 weak single-view detections dropped on `with_ceiling`).

Tests also caught two area bugs: a 1-cell grid border let closing grow every footprint by one ring (+17 % on a 0.06 m² patch), and sparsely sampled points under-measured area (0.16 vs 0.49 m²).

## Rules (C11)

Conditions: class, surface kind, area range, length range (cracks), lowest point above the floor (walls). Each match adds a flag (`severity`, `reason`, `recommendation`, `rule_id`) and scope items whose quantities are formulas over `area`, `length`, `surface_area`, `wall_length`, `ceiling_height`, `room_area` with `min`/`max`/`ceil`/`sqrt`. Formulas are parsed with Python's `ast` and only whitelisted names, operators and functions run. Quantity σ is propagated by evaluating the formula at area ± σ.

## Tests (`tests/test_damage.py`, 20)

Threshold / decoy / size filtering; box mapping back to sensor orientation for all four rotations; a synthetic stain's mask within 15 % of its area; clean wall → no mask; a line along a wall/ceiling boundary → `edge`; stain on non-surface pixels ignored; footprint area of a 0.2 × 0.3 m patch; a 0.7 × 0.7 m floor stain projected from 1.4 m measures 0.49 m² within 12 %; view fusion, weak single view dropped, class vote; ceiling stain → R01 with patch (min 0.5 m²) and full-ceiling repaint (12 m²); low vs high wall stains → R02 / R03; long vs short cracks → R05 / R07; σ propagation; every rule well formed; formulas cannot execute code.

## Known limits

- Recall is unmeasured: the sample captures contain no damage. The staged-damage benchmark room (water stain + crack) will measure detection and area error, and calibrate the 0.18 threshold.
- Thin cracks are close to the depth resolution; their area is less meaningful than their length.
- Classical refinement assumes damage differs in colour from a fairly uniform surface; patterned wallpaper or tiles would need SAM 2.
