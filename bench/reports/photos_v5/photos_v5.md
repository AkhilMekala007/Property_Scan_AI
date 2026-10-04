# Benchmark: photos_v5 (photo tier)

| Item | Ours id | Truth | Ours [90 % interval] | Error | Inside | Gate | Pass | App | App error | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| room_1.ceiling | room_1.ceiling | 2.890 | 3.803 [3.497, 4.108] | +91.3 cm | no | +-8 % (stand-in) | ✗ | 3.000 | +11.0 cm | loss |
| room_1.dim0 | room_1.wall_15 | 3.350 | 4.217 [3.841, 4.594] | +86.7 cm | no | +-8 % | ✗ | 3.400 | +5.0 cm | loss |
| room_1.dim1 | room_1.wall_8 | 3.010 | 2.400 [2.118, 2.682] | -61.0 cm | no | +-8 % | ✗ | 3.100 | +9.0 cm | loss |
| room_2.ceiling | — | 2.940 | — | missed |  | +-8 % (stand-in) | ✗ |  |  |  |
| room_2.dim0 | — | 3.750 | — | missed |  | +-8 % | ✗ |  |  |  |
| room_2.dim1 | — | 3.010 | — | missed |  | +-8 % | ✗ |  |  |  |
| room_3.ceiling | room_3.ceiling | 3.000 | 2.743 [2.438, 3.049] | -25.7 cm | yes | info (approximate truth) | — |  |  |  |
| room_3.dim0 | room_3.wall_5 | 3.890 | 3.924 [3.557, 4.290] | +3.4 cm | yes | +-8 % | ✓ | 4.100 | +21.0 cm | win |
| room_3.dim1 | room_3.wall_8 | 3.030 | 2.925 [2.611, 3.239] | -10.5 cm | yes | +-8 % | ✓ | 3.000 | -3.0 cm | loss |
| kitchen.ceiling | kitchen.ceiling | 2.900 | — | missed |  | +-8 % (stand-in) | ✗ |  |  |  |
| kitchen.dim0 | kitchen.wall_6 | 3.000 | 3.200 [2.870, 3.530] | +20.0 cm | yes | +-8 % | ✓ | 3.000 | +0.0 cm | loss |
| kitchen.dim1 | kitchen.wall_5 | 2.500 | 2.225 [1.954, 2.496] | -27.5 cm | no | info (approximate truth) | — |  |  |  |
| hall.area | hall.floor_area | 36.000 | 40.517 [37.521, 43.513] | +4.52 m2 | no | info (no area gate in the brief) | — | 34.600 | -1.40 m2 | loss |
| hall.ceiling | hall.ceiling | 2.500 | 2.724 [2.419, 3.030] | +22.4 cm | yes | +-8 % (stand-in) | ✗ | 2.600 | +10.0 cm | loss |
| hall.dim0 | hall.wall_16 | 8.500 | 4.572 [4.164, 4.980] | -392.8 cm | no | info (approximate truth) | — |  |  |  |
| hall.dim1 | hall.wall_9 | 3.500 | 4.126 [3.756, 4.496] | +62.6 cm | no | info (approximate truth) | — |  |  |  |

## Summary

```
{
  "wall": {
    "passed": 3,
    "total": 7,
    "rate": 0.429
  },
  "ceiling": {
    "passed": 0,
    "total": 4,
    "rate": 0.0
  },
  "opening": {
    "passed": 0,
    "total": 0,
    "rate": null
  },
  "missed": 4,
  "interval_coverage": 0.571,
  "median_abs_error_m": {
    "wall": 0.2,
    "ceiling": 0.5686,
    "area": 4.517
  },
  "median_abs_error_pct": {
    "wall": 6.67,
    "ceiling": 20.28,
    "area": 12.55
  },
  "head_to_head": {
    "beat_or_tie": 1,
    "total": 8,
    "rate": 0.125,
    "pass": false
  }
}
```
