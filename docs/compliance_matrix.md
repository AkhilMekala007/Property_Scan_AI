# Compliance matrix

Requirement (from the brief) → where it is met → artifact → status. Status as of 2026-10-04 05:00 IST;
✅ met, 🟡 partly met / pending capture, ❌ not met (with the reason).

## Part 1 — capture and tiers

| # | Requirement | File / command | Artifact | Status |
|---|---|---|---|---|
| 1.1 | Capture route (Route 2: stock apps + one-page protocol a non-engineer follows) | `docs/capture_protocol.md` | One page: install, walk, how long, avoid, hand-over, run | ✅ |
| 1.2 | Photos tier: 2–8 stills per room, one folder per room, no depth/poses | `scan/fragments.py` (`photo_fragments`), `scan/multiview.py` | `outputs/photos_flat/result.json`, `plan.png` | ✅ runs; per-room accuracy in gate (see 2.x) |
| 1.3 | Video tier: handheld walkthrough, any iPhone 15+ | `scan/fragments.py` (`video_fragments_da3`) | `outputs/video_flat/result.json` | 🟡 runs end to end; fails its gate (fix loop, 4.x) |
| 1.4 | LiDAR tier: depth, poses, intrinsics on Pro devices | `scan/adapters/stray.py`, `scan/pipeline.py` | `bench/runs/blind_v1/lidar_flat/` | ✅ |
| 1.5 | Same output contract from each tier | `scan/contract/`, `schema/result.schema.json` | one schema, validated for all three tiers | ✅ |
| 1.6 | Intervals widen honestly as sensor data thins | `scan/contract/budget.py`, `scan/calibrate.py` | per-tier budgets; photo intervals ~4× LiDAR | ✅ |
| 1.7 | Device matrix: tier × hardware × honest accuracy | `docs/device_matrix.md` | table | 🟡 update with benchmark numbers |

## Part 2 — output contract and gates

| # | Requirement | File / command | Artifact | Status |
|---|---|---|---|---|
| 2.1 | Per-room plan: walls, ceiling height, floor area, openings | `scan/measure`, `scan/openings` | `result.json` rooms / openings | ✅ |
| 2.2 | Stitched multi-room plan with correct adjacency | `scan/stitch`, `scan/fragments.py` (`place_fragments`) | `plan.png` | ✅ LiDAR · ❌ photos (rooms not joined: doors not detected) · ❌ video |
| 2.3 | Per-surface damage regions with class and metric extent | `scan/damage` | `result.json` damage | 🟡 implemented; staged-damage capture pending |
| 2.4 | Concealed-damage flags with the rule that fired | `scan/rules`, `rules/damage_rules.yaml` | `result.json` flags (rule_id, reason) | 🟡 implemented; needs the staged-damage capture |
| 2.5 | Scope line items keyed to surfaces | `scan/rules` | `result.json` scope (surface_id) | 🟡 as 2.4 |
| 2.6 | Confidence interval on every measurement | `scan/contract/build.py` | every number is a `Measure` {value, lo, hi, sigma, calibrated} | ✅ |
| 2.7 | One command per capture | `scan run <folder>` | tier auto-detected | ✅ |
| 2.8 | JSON to the published schema | `scan schema` → `schema/result.schema.json` | test fails if models drift from schema | ✅ |
| 2.9 | Rendered plan | `scan/stitch/render.py` | `plan.png` | ✅ |
| 2.10 | Benchmark: multi-room capture, ≥3 rooms + connector | `data/raw/benchmark/*_flat` | 3BHK, 5 spaces, all tiers | ✅ |
| 2.11 | Benchmark: furnished room with staged damage, two classes | — | — | ❌ pending morning capture |
| 2.12 | Benchmark: same rooms at all three tiers | `data/raw/benchmark/{lidar,video,photos}_flat` | | ✅ |
| 2.13 | Benchmark: one room captured twice at the same tier | — | — | ❌ pending morning capture |
| 2.14 | Laser/tape ground truth on everything; raw data submitted | `bench/ground_truth/flat_3bhk.yaml` | tape GT (rooms, ceilings); doors/windows missing | 🟡 openings GT pending |
| 2.15 | Gate: opening widths ≤ 2 cm on ≥ 85 %, detection scored | `scan/bench.py` (opening rows) | — | ❌ no opening GT yet |
| 2.16 | Gate: ceiling ≤ 1.5 cm per room | `bench/reports/*` | LiDAR 3/4 (hall −1.8 cm) | 🟡 |
| 2.17 | Gate: repeatability ≤ 1 cm or 0.5 % per wall | `scan repeat` (proxy), — | proxy only | ❌ needs the repeat capture |
| 2.18 | Drift accountability + ablation on/off | `scan/drift`, `scan drift` ablation | `docs/LLD/09_drift.md` (sample data) | 🟡 rerun on the 3BHK |
| 2.19 | Photo-tier whole-property stitch: no overlaps, footprint ±8 %, calibrated | `scan/fragments.py` | — | ❌ rooms not joined (see known failure modes) |
| 2.20 | Photo walls ±8 % with calibrated intervals | `bench/reports/photos_flat*` | 7/7 walls, median 0.9 % | ✅ |
| 2.21 | Video walls ±3 % | `bench/reports/video_flat` | no room matched | ❌ (fix loop) |
| 2.22 | Calibration scored at every tier; no confident garbage | `scan calibrate`, `calibration/factors.json` | LiDAR / photo factors, leave-one-out coverage | 🟡 video uncalibrated (no matched rows) |

## Part 3 — head-to-head

| # | Requirement | File / command | Artifact | Status |
|---|---|---|---|---|
| 3.1 | LiDAR tier vs a consumer app on ≥ 2 rooms; app name + version; export submitted | `bench/ground_truth/flat_3bhk.yaml` (competitor) | `bench/reports/lidar_flat.md` | ✅ Polycam, 5 rooms |
| 3.2 | One table, our error and theirs, dimension by dimension; beat/tie ≥ 70 % | `scan/bench.py` | 11/12 = 92 % | ✅ |

## Part 4 — fix loop

| # | Requirement | File | Artifact | Status |
|---|---|---|---|---|
| 4.1 | Worst gate with failing number | `docs/fix_loop.md` | video walls ±3 %: 0 rooms matched | ✅ |
| 4.2 | Root-cause hypothesis + evidence | `docs/fix_loop.md` | scale drift 13–33 % between chunks | ✅ |
| 4.3 | Fix + predicted number, recorded before the result | `docs/fix_loop.md` + git history | 3 attempts, timestamps | ✅ |
| 4.4 | Shipped fix, before/after regenerable, readable diff | commits `6ec0cd4` → `2fea3a4` → current | | 🟡 prediction falsified; write-up pending |

## Part 5 and deliverables

| # | Requirement | File | Status |
|---|---|---|---|
| 5.1 | Commit history as work happens | git log | ✅ |
| D3 | README: fresh capture → result in < 15 min on a clean machine | `README.md` | ❌ to write + clean-machine test |
| D4 | Reproduction bundle (regenerate every number; cache replays deterministically; live path runs) | — | ❌ to build |
| D5 | Benchmark report: all tiers, repeatability, head-to-head, timing | `bench/reports/` | 🟡 repeatability + timing pending |
| D7 | Technical report ≤ 6 pages | `docs/technical_report.md` | ❌ in progress |
| D8 | Raw benchmark data: sensor logs, GT, app exports | `data/raw/benchmark/` (address/GPS redacted before publishing) | 🟡 redaction pending |
| C1 | Runs without calling our infrastructure; models disclosed | `scan/models.py`, `result.json` processing.models | ✅ offline; all models listed with licences |
| C2 | Weights fetched by script | `scripts/fetch_weights.py` | 🟡 add DA3 weights + pinned install |
| C3 | Mirrors, glass, wet-look surfaces, low light covered | QC (`scan/qc`), semantics (mirror class excluded from structure) | 🟡 to document in the report |
