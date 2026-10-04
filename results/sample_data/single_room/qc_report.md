# QC report: c00a170fe1

| | |
|---|---|
| Tier | lidar |
| Quality score | 0.92 |
| Keyframes kept | 217 / 238 |
| Kept, but image blurry (geometry only) | 2 |
| Median brightness | 147 / 255 |
| Floor seen | True (16.3 m²) |
| Ceiling seen | False (0.0 m²) |
| Camera height above floor | 1.39 m |
| Frames looking up | 0% |
| Tracking jumps | 0 |

## Issues

- **WARNING `CEILING_NOT_SEEN`**: ceiling was not observed, so ceiling heights will be reported as unavailable
  - Fix: In each room, tilt the phone up toward the ceiling for about 5 seconds.
- **INFO `DEVICE_UNKNOWN`**: iPhone model not recorded; device matrix check skipped
  - Fix: Pass --device, e.g. --device "iPhone 15 Pro".

## Dropped frames by reason

- fast_motion: 21
