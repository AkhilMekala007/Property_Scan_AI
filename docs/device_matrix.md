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

| Quantity | Tier | Gate (brief) | 90 % interval (calibrated) | Measured on the 3BHK benchmark (tape) |
|---|---|---|---|---|
| Wall length | LiDAR (17 Pro) | ≤ 1 cm or 0.5 % (stand-in) | about ±3–9 cm | median 0.5 %; 4/7 in gate; worst +15.6 cm (wardrobe front) |
| Ceiling height | LiDAR | ≤ 1.5 cm | about ±1.7 cm | median 0.4 %; 3/4 in gate (hall −1.8 cm) |
| Opening width | LiDAR | ≤ 2 cm on ≥ 85 % | ±2.5 cm | not scored (no opening ground truth) |
| Wall length | Video (15) | ±3 % | wide, uncalibrated | fails: 0/10 matched (best capture: Room_1 −9 %) |
| Wall length | Photo (15, 1×, landscape) | ±8 % | about ±0.3 m | median 0.9 %; 7/7 in gate |
| Ceiling height | Photo | — | about ±0.3–0.5 m | median 3.1 %; 3/4 |
| Whole-property stitch | Photo | joined, no overlaps, ±8 % footprint | — | fails: rooms measured but unconnected |
| Repeatability | Photo | ≤ 1 cm or 0.5 % | — | fails: scale varies 8–15 % between captures |

Capture conditions that measurably hurt the camera tiers (protocol forbids them): 0.5× ultra-wide lens
(up to 11 % small), portrait orientation (scale unstable), close-ups along walls (too little floor).

Known limits that widen intervals or remove measurements (reported in `result.json`, never hidden): ceiling not filmed (`ceiling_height: null`), walls not observed (`observed: false`), door heads not filmed (lower bound only), curtains over windows (`covered`), low light and fast motion (QC warnings, lower quality score).
