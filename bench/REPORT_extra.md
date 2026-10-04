## Repeatability (same room, same tier, two captures)

Room_3, photo tier: first capture `photos_v4/Room_3` (8 photos) vs repeat `photos_repeat/Room_3`
(taken independently, 6 photos, then 8). Results: `outputs/photos_v4/result.json`, `outputs/Room_3/result.json`.

| Dimension | Capture 1 | Repeat (6 photos) | Repeat (8 photos) | Tape | Gate (1 cm / 0.5 %) |
|---|---|---|---|---|---|
| Length (longest wall) | 3.924 | 2.156 (partial room) | 4.477 | 3.89 | ✗ |
| Width | 2.925 | 2.969 | 2.951 | 3.03 | ✗ (+2.6 cm) |
| Ceiling | 2.744 | 2.931 | 3.152 | ~3.0 (LiDAR 2.932) | ✗ |

**Verdict: unrepeatable** at the photo tier. The metric scale (DA3METRIC on the chosen photos) varies by
about 8–15 % between captures; within our ±8 % photo intervals but far outside the 1 cm gate. LiDAR-tier
repeatability is measured only by the pose-perturbation proxy (`docs/LLD/10_repeatability.md`): wall spread
median 3.9–16 cm under 5 mm / 0.1° perturbation — also unrepeatable, from room-assembly instability (see
the drift ablation below and `docs/fix_loop.md`).

## Drift accountability (LiDAR, 3BHK)

`scan drift data/raw/benchmark/lidar_flat` → `outputs/36c50efbbe/drift_ablation.json`. No revisit
overlaps enough for a loop closure (26 tested); plane-anchored heading drift measured median 0.42°, max
0.85°.

| | Correction off | Correction on (forced) |
|---|---|---|
| Wall voxels on fitted planes | 0.753 | 0.82 |
| Shared walls | 3 | 5 |
| Footprint | 80.9 m² | 79.3 m² |
| Tape walls in gate | 4/7 | 2/7 |

Shipped rule: drift is measured and reported on every capture; the heading correction is applied above
1° (`docs/LLD/09_drift.md`).

## Staged damage

One class staged (coffee stain with drips as a water stain, 24 × 18 cm, Room_1, `photos_damage`). Result:
**not detected** (0 detections over 5 photos). The detector rejects look-alikes with decoy prompts
("a poster", "a picture frame"); a stain on a taped sheet of paper is close to those decoys. The second
class was not staged. Damage benchmark row: **not met**.

## Timing (cold, i5-1135G7, 8 GB, CPU only)

| Capture | Runtime |
|---|---|
| LiDAR 3BHK (163 s scan, 8058 frames) | ~6 min first run; 70 s with cached labels |
| Photos, 5 rooms (37 photos) | 22–27 min (DA3 dominates) |
| Video 283 s, room mode | 25 min |
| Video 283 s, chunk mode (superseded) | 81 min |
