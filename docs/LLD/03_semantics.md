# LLD 03 — C4 Semantics

**Goal:** label every pixel of the selected keyframes as wall, floor, ceiling, door, window, mirror, person or other, so later steps measure the room and not the objects in it (HLD §5.2, C4).

```
FrameSet (after QC) ──► run_semantics ──► FrameSet (selected frames gain labels() / label_conf())
                                     └─► SemanticSummary ──► outputs/<capture>/semantics_summary.json
                                     └─► overlays         ──► outputs/<capture>/labels/overlay_*.jpg
```

## Modules

| File | Responsibility |
|---|---|
| `scan/models.py` | Registry of pretrained models (repo, revision, licence); `require_weights` loads from `weights/` only |
| `scripts/fetch_weights.py` | One-time download into `weights/`; the only network use in the project |
| `scan/semantics/classes.py` | Our 8 `Surface` classes, ADE20K name → Surface mapping, colours |
| `scan/semantics/upright.py` | Quarter turns that make an image upright, from the camera pose |
| `scan/semantics/segmenter.py` | `Segmenter` interface; `SegFormerSegmenter` (CPU, local weights) |
| `scan/semantics/check.py` | Label-vs-geometry self-check; `labels_at` resize helper |
| `scan/semantics/__init__.py` | Frame selection, label cache, `run_semantics`, summary, overlays |
| `scan/core/geometry.py` | Added `organised_points_normals` (shared with QC coverage) |

## Pipeline per frame

1. **Select** frames with `rgb_ok` (QC marks blurry images), at most 100, evenly spread over the walk. Unlabelled frames stay in the FrameSet for geometry.
2. **Upright:** world-up in camera coordinates gives the quarter turn (`np.rot90` k) that puts up at the top. All sample frames needed k = 3 (phone held in portrait).
3. **Segment** at 320 px short side → ADE20K softmax at 1/4 resolution.
4. **Aggregate** ADE probabilities per Surface (wall + painting + poster all vote wall), then upsample the 8 channels to the label size and take the arg-max; confidence = winning Surface probability.
5. **Rotate back** to sensor orientation; store at 480×360 as PNG (labels) + PNG (confidence × 255).

**Cache:** `outputs/cache/<capture>/labels/<model>@<size>/`, keyed by a manifest of model, input size, mapping version and label size; any change clears it.

## Class mapping

| Surface | ADE20K classes |
|---|---|
| wall | wall, column, painting, poster, bulletin board |
| floor | floor, rug |
| ceiling | ceiling |
| door | door, screen door |
| window | windowpane, curtain, blind |
| mirror | mirror |
| person | person |
| other | everything else. ADE20K `glass` is a drinking glass and stays here |

## Model choice (measured on `single_scan_with_ceiling`)

| Model @ short side | s / image (CPU) | Agreement with B2@512 | Mirror found in test frame |
|---|---|---|---|
| B0 @ 512 | 0.7 | — | fragments only |
| B2 @ 512 | 4.0 | 100 % | 39 % of frame |
| **B2 @ 320 (default)** | **0.93** | **90 %** | **41 %** |
| B2 @ 256 | 0.60 | 86 % | 44 % |

B2 @ 320 keeps B2's clearly better door and mirror labels at a quarter of the cost.

## Self-check against geometry

On up to 40 labelled frames, depth gives world points and normals on a stride-2 grid. A label agrees if: wall → vertical surface (|n·up| < 0.3); floor → horizontal surface > 0.5 m below the camera; ceiling → horizontal surface > 0.3 m above the camera. `LOW_LABEL_AGREEMENT` is raised below 60 % (with > 2000 pixels checked). This is the C4 component metric for the benchmark harness.

## Results on the sample captures

| Capture | Labelled | First run | Cached rerun | Wall | Floor | Ceiling | Mirror frames |
|---|---|---|---|---|---|---|---|
| `single_room` | 100 | 137 s | 9.6 s | 92 % | 93 % | — (163 px) | 9 |
| `single_scan_floor_only` | 100 | 157 s | — | 94 % | 96 % | — (none seen) | 10 |
| `single_scan_with_ceiling` | 100 | 150 s | — | 94 % | 80 % | 87 % | 15 |

Times include ingest and QC. Overlays checked by eye: curtains → window, sofa → other, tiles → floor, corridor ceiling → ceiling, painting → wall.

## Known limits

- **Mirrors are only partly caught by labels.** In one overlay a mirror reflecting the person capturing was labelled wall, with the reflection labelled person and floor. Planned geometric safety net in C7: depth points behind a fitted wall plane are rejected.
- First-run labelling costs ~1.4 s per frame end to end; `--max-frames` trades coverage for time.
- Labels are per frame; combining them across views happens in C7 on 3D surfaces.
- SegFormer weights are non-commercial (disclosed). Mask2Former Swin-T (MIT) is the documented swap via the `Segmenter` interface.
