# Device matrix

Which tier runs on which iPhone, and what accuracy each tier honestly delivers.

| Device | LiDAR tier | Video tier | Photo tier |
|---|---|---|---|
| iPhone 15, 15 Plus, 16, 16 Plus, 16e, 17, Air | ❌ no LiDAR sensor | ✅ | ✅ |
| iPhone 15 Pro / Pro Max, 16 Pro / Pro Max, 17 Pro / Pro Max | ✅ (Stray Scanner) | ✅ | ✅ |
| Older than iPhone 15 | Runs, flagged `DEVICE_UNSUPPORTED`, wider intervals | same | same |
| Unknown model (not recorded in the files) | Runs, flagged `DEVICE_UNKNOWN`; pass `--device` | same | same |

Devices used to build the benchmark: **iPhone 17 Pro** (LiDAR, video, photos) and **iPhone 15** (video, photos).

## Accuracy per tier

Targets are the brief's gates. **Measured** columns are filled from the benchmark (`scan bench`) and are the only numbers we claim.

| Quantity | Tier | Gate (brief) | Current interval (uncalibrated budget) | Measured error on benchmark |
|---|---|---|---|---|
| Wall length | LiDAR | ≤ 1 cm or 0.5 % (repeatability) | ±(1 cm + 0.3 %) fit + budget | *pending benchmark* |
| Ceiling height | LiDAR | ≤ 1.5 cm; spread ≤ 1 cm | ±1.7 cm | *pending* (repeat spread on sample: 0.6 cm) |
| Opening width | LiDAR | ≤ 2 cm on ≥ 85 % | ±2.5 cm | *pending* |
| Wall length | Video | ±3 % | ±(3 cm + 1.5 %) | *pending* |
| Wall length / footprint | Photo | ±8 % | ±(6 cm + 4 %) | *pending* |

Known limits that widen intervals or remove measurements (reported in `result.json`, never hidden): ceiling not filmed (`ceiling_height: null`), walls not observed (`observed: false`), door heads not filmed (lower bound only), curtains over windows (`covered`), low light and fast motion (QC warnings, lower quality score).
