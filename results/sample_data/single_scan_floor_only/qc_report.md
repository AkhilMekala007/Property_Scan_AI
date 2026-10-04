# QC report: 1a8384c3f6

| | |
|---|---|
| Tier | lidar |
| Quality score | 0.81 |
| Keyframes kept | 395 / 400 |
| Kept, but image blurry (geometry only) | 6 |
| Median brightness | 146 / 255 |
| Floor seen | True (43.4 m²) |
| Ceiling seen | False (0.0 m²) |
| Camera height above floor | 1.40 m |
| Frames looking up | 0% |
| Tracking jumps | 1 |

## Issues

- **WARNING `TRACKING_JUMP`**: 1 camera tracking jump(s) at t = 113.6 s; nearby frames dropped
  - Fix: Avoid covering the camera and pointing at blank walls up close.
- **WARNING `CEILING_NOT_SEEN`**: ceiling was not observed, so ceiling heights will be reported as unavailable
  - Fix: In each room, tilt the phone up toward the ceiling for about 5 seconds.
- **INFO `DEVICE_UNKNOWN`**: iPhone model not recorded; device matrix check skipped
  - Fix: Pass --device, e.g. --device "iPhone 15 Pro".

## Dropped frames by reason

- fast_motion: 4
- near_tracking_jump: 2
