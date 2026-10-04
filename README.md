# Property Scan AI

Handheld iPhone capture → dimensioned, stitched whole-property floor plan with
per-surface damage regions, concealed-damage flags, scope line items, and a
calibrated confidence interval on every measurement.

Three input tiers, one output contract:

| Tier | Input | Hardware |
|------|-------|----------|
| Photos | 6–8 stills per room (brief: 2–8), 1× lens, landscape, one folder per room | Any iPhone 15+ |
| Video | Handheld walkthrough clip | Any iPhone 15+ |
| LiDAR | Depth + poses + intrinsics | Pro-class iPhones |

Design: [docs/HLD.md](docs/HLD.md) · [visual overview](docs/HLD_visual.html) · LLDs in [docs/LLD](docs/LLD) ·
[technical report](docs/technical_report.md) · [compliance matrix](docs/compliance_matrix.md) ·
capture: [protocol](docs/capture_protocol.md), [device matrix](docs/device_matrix.md).

## Results on our benchmark (3BHK flat, tape ground truth)

| Tier | Walls | Ceilings | Notes |
|---|---|---|---|
| LiDAR (iPhone 17 Pro) | median error 0.5 %, 4/7 in 1 cm / 0.5 % | 3/4 in 1.5 cm | beats or ties Polycam on 11/12 dimensions |
| Photos (iPhone 15) | **7/7 in ±8 %**, median 0.9 % | 3/4 | rooms measured; whole-property stitch fails (unconnected) |
| Video (iPhone 15) | fails ±3 % | — | documented fix loop: `docs/fix_loop_declaration.md` |

**Provided sample data:** outputs for all three captures (JSON, plan, QC report) in [results/sample_data/](results/sample_data/README.md).

Full tables: [bench/REPORT.md](bench/REPORT.md) (generated from data). Gate-by-gate status:
[docs/compliance_matrix.md](docs/compliance_matrix.md).

## Setup

Requires Python 3.11+ (tested on 3.14, Windows, CPU only).

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows; use .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
pip install -e .
# Depth Anything 3 (photo / video tiers), pinned commit, its own dependencies come from requirements.txt
pip install --no-deps --ignore-requires-python "git+https://github.com/ByteDance-Seed/Depth-Anything-3@3d835ec1a5802d64a8b8b15f817a1ab54809bfe4"
python scripts/fetch_weights.py   # one-time model download (~2.3 GB); the pipeline itself runs offline
```

Measured on a clean machine (fresh clone, new environment, home connection): **about 25 min of setup** — 16.5 min `pip install` (torch, open3d, transformers, no cache), 1.5 min for the package and DA3, 6.6 min for the 2.3 GB of weights — then **6 min for the first photo room**, cold. Almost all of it is download time; on a faster connection or with a local wheel cache (`pip download -r requirements.txt -d wheels`, then `pip install --no-index --find-links wheels -r requirements.txt`) setup is much shorter.

Models (weights in `weights/`, never committed; every `result.json` lists the ones it used):

| Model | Purpose | Licence |
|---|---|---|
| Depth Anything 3 BASE (`depth-anything/DA3-BASE`) | Photo / video tiers: camera poses + depth from unposed images | Apache-2.0 |
| Depth Anything 3 METRIC-LARGE (`depth-anything/DA3METRIC-LARGE`) | Photo / video tiers: metric scale (focal-aware) | Apache-2.0 |
| SegFormer-B2, ADE20K (`nvidia/segformer-b2-finetuned-ade-512-512`) | Surface labels: wall, floor, ceiling, door, window, mirror | NVIDIA Source Code License (non-commercial) |
| SegFormer-B0, ADE20K | Faster alternative (`--model segformer-b0-ade`) | same |
| OWL-ViT B/32 (`google/owlvit-base-patch32`) | Damage detection from text prompts | Apache-2.0 |
| OWLv2 base, SAM 2.1 tiny | Registered alternatives (slower on CPU; not used by default) | Apache-2.0 |

## Run a capture (one command)

```bash
scan run path/to/capture --device "iPhone 15 Pro"
```

Writes to `outputs/<capture_id>/`:

| File | Contents |
|---|---|
| `result.json` | The full result, validated against [`schema/result.schema.json`](schema/result.schema.json): rooms (walls, ceiling height, floor area, openings), stitched plan (adjacency, shared walls, footprint), damage regions, concealed-damage flags with the rule that fired, scope line items keyed to surfaces. Every number carries a 90 % interval (`value`, `lo`, `hi`, `sigma`) |
| `plan.png` | The rendered whole-property floor plan |
| `qc_report.md` / `.json` | Capture quality issues with fixes |

`scan run` exits 1 when the capture has QC errors or rooms overlap (the files are still written).
Intervals use the C12 factors in `calibration/factors.json` when present (`"calibrated": true` on those
quantities); everything else keeps the provisional error budget (`"calibrated": false`).
`scan schema` regenerates the published schema from the models.

Runtime, cold, on the reference laptop (i5-1135G7, 8 GB, CPU only): LiDAR ~6 min, photos ~22–27 min
(5 rooms), video ~25–35 min (DA3 on CPU dominates; a GPU makes photo and video a few minutes).

## Benchmark, calibration (regenerate every reported number)

```bash
scan run data/raw/benchmark/lidar_flat --device "iPhone 17 Pro"
scan run data/raw/benchmark/photos_flat
scan run data/raw/benchmark/video_flat --device "iPhone 15"
scan bench outputs/<capture>/result.json bench/ground_truth/flat_3bhk.yaml --capture <capture> --out bench/reports/<capture>
scan calibrate bench/reports/*/*.json --out calibration/factors.json
```

The frozen blind LiDAR run (made before the ground truth was shared) is in `bench/runs/blind_v1`.

## Step-by-step commands (debugging)

Inspect a capture: detects the tier, validates the files, selects keyframes and
prints a summary. `--topdown` also saves a top-down sanity render to `outputs/`.

```bash
scan inspect path/to/capture --topdown
```

Quality-check a capture (writes `outputs/<capture>/qc_report.md`):

```bash
scan qc path/to/capture --device "iPhone 15 Pro"
```

Label surfaces in each frame (writes overlays and `semantics_summary.json`):

```bash
scan labels path/to/capture --device "iPhone 15 Pro"
```

Fuse the capture into a 3D model and find floor, ceiling and wall planes
(writes `structure.json` and a top-down `structure_debug.png`):

```bash
scan structure path/to/capture --device "iPhone 15 Pro"
```

Split the capture into rooms, corridors and doorways (writes `rooms.json` and `rooms_debug.png`):

```bash
scan rooms path/to/capture --device "iPhone 15 Pro"
```

Measure every room: wall lengths, floor area, ceiling height with uncertainties
(writes `measurements.json` and a dimensioned `plan.png`):

```bash
scan measure path/to/capture --device "iPhone 15 Pro"
```

Find doors, windows and open passages with widths and heights (writes `openings.json`
and `plan_openings.png`; `--elevations` also saves a front view of every wall):

```bash
scan openings path/to/capture --device "iPhone 15 Pro"
```

Stitch everything into one whole-property plan (writes `plan.json` and `floor_plan.png`;
exits 1 if rooms still overlap):

```bash
scan plan path/to/capture --device "iPhone 15 Pro"
```

Drift correction (pose graph with ICP loop closures) is on by default; `--no-drift-fix` uses
ARKit poses as-is. The ablation runs both and compares them (writes `drift_ablation.json`,
both floor plans and an overlay):

```bash
scan drift path/to/capture
```

Repeatability under tiny pose perturbations (room counts, area / wall / opening spread):

```bash
scan repeat path/to/capture --runs 3
```

Detect and measure damage, then apply the concealed-damage and scope rules
(`rules/damage_rules.yaml`; writes `damage.json`):

```bash
scan damage path/to/capture --device "iPhone 15 Pro"
```

## Tests

```bash
pytest              # fast unit tests on synthetic captures
pytest -m sample    # also load the provided sample captures (slow, needs the data locally)
```

## Benchmark data (privacy)

**Download:** [property_scan_benchmark_v1.zip](https://github.com/AkhilMekala007/Property_Scan_AI/releases/download/benchmark-v1/property_scan_benchmark_v1.zip)
(1.13 GB, GitHub release `benchmark-v1`; SHA-256 `a1285ec78c422ae151ca65510cd4c2e130800314b9cad2f7a85e84ecbb71e7ca` — check with `certutil -hashfile property_scan_benchmark_v1.zip SHA256` on Windows or `sha256sum` elsewhere). The data is kept out of git on purpose (the repo stays small); unzip so the captures sit in `data/raw/benchmark/`:

```bash
python -c "import zipfile; zipfile.ZipFile('property_scan_benchmark_v1.zip').extractall('data/tmp')"
```
then move `data/tmp/benchmark/*` to `data/raw/benchmark/`. Contents: the 3BHK LiDAR scan (Stray Scanner),
the iPhone 15 videos and photo sets used in `bench/REPORT.md`, the repeat-room and staged-damage photos,
Polycam's floor-plan export, the tape ground truth and the redaction log.


Raw captures stay in `data/raw/` (never committed). `python scripts/redact_release.py` builds a publishable
copy in `data/release/`: GPS fields in photo EXIF zeroed in place (pixels verified identical, so every
number reproduces), video location tags blanked, Polycam altitude / compass rows removed, and the Polycam
spatial report and raw zip (which contain coordinates) withheld. `python scripts/make_bench_report.py`
regenerates `bench/REPORT.md` from the benchmark JSON.
