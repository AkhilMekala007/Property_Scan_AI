# Benchmark: photos_flat (photo tier)

| Item | Ours id | Truth | Ours [90 % interval] | Error | Inside | Gate | Pass | App | App error | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| room_1.ceiling | room_1.ceiling | 2.890 | 2.840 [2.708, 2.971] | -5.0 cm | yes | +-8 % (stand-in) | ✓ | 3.000 | +11.0 cm | win |
| room_1.dim0 | room_1.wall_0 | 3.350 | 3.339 [3.020, 3.657] | -1.1 cm | yes | +-8 % | ✓ | 3.400 | +5.0 cm | win |
| room_1.dim1 | room_1.wall_9 | 3.010 | 3.025 [2.716, 3.334] | +1.5 cm | yes | +-8 % | ✓ | 3.100 | +9.0 cm | win |
| room_2.ceiling | room_2.ceiling | 2.940 | 2.912 [2.780, 3.044] | -2.8 cm | yes | +-8 % (stand-in) | ✓ | 3.000 | +6.0 cm | win |
| room_2.dim0 | room_2.wall_3 | 3.750 | 3.699 [3.347, 4.051] | -5.1 cm | yes | +-8 % | ✓ | 3.900 | +15.0 cm | win |
| room_2.dim1 | room_2.wall_2 | 3.010 | 3.206 [2.885, 3.526] | +19.6 cm | yes | +-8 % | ✓ | 3.700 | +69.0 cm | win |
| room_3.ceiling | room_3.ceiling | 3.000 | 2.743 [2.612, 2.875] | -25.7 cm | no | info (approximate truth) | — |  |  |  |
| room_3.dim0 | room_3.wall_5 | 3.890 | 3.924 [3.557, 4.290] | +3.4 cm | yes | +-8 % | ✓ | 4.100 | +21.0 cm | win |
| room_3.dim1 | room_3.wall_8 | 3.030 | 2.925 [2.611, 3.239] | -10.5 cm | yes | +-8 % | ✓ | 3.000 | -3.0 cm | loss |
| kitchen.ceiling | kitchen.ceiling | 2.900 | 2.771 [2.639, 2.902] | -13.0 cm | yes | +-8 % (stand-in) | ✓ | 3.000 | +10.0 cm | loss |
| kitchen.dim0 | kitchen.wall_4 | 3.000 | 2.980 [2.685, 3.275] | -2.0 cm | yes | +-8 % | ✓ | 3.000 | +0.0 cm | loss |
| kitchen.dim1 | kitchen.wall_3 | 2.500 | 2.199 [1.942, 2.456] | -30.1 cm | no | info (approximate truth) | — |  |  |  |
| hall.area | hall.floor_area | 36.000 | 55.795 [52.060, 59.530] | +19.80 m2 | no | info (no area gate in the brief) | — | 34.600 | -1.40 m2 | loss |
| hall.ceiling | hall.ceiling | 2.500 | 2.802 [2.671, 2.934] | +30.2 cm | no | +-8 % (stand-in) | ✗ | 2.600 | +10.0 cm | loss |
| hall.dim0 | hall.wall_6 | 8.500 | 5.025 [4.580, 5.470] | -347.5 cm | no | info (approximate truth) | — |  |  |  |
| hall.dim1 | hall.wall_13 | 3.500 | 4.883 [4.463, 5.302] | +138.2 cm | no | info (approximate truth) | — |  |  |  |

## Summary

```
{
  "wall": {
    "passed": 7,
    "total": 7,
    "rate": 1.0
  },
  "ceiling": {
    "passed": 3,
    "total": 4,
    "rate": 0.75
  },
  "opening": {
    "passed": 0,
    "total": 0,
    "rate": null
  },
  "missed": 0,
  "interval_coverage": 0.909,
  "median_abs_error_m": {
    "wall": 0.0335,
    "ceiling": 0.0898,
    "area": 19.795
  },
  "median_abs_error_pct": {
    "wall": 0.86,
    "ceiling": 3.1,
    "area": 54.99
  },
  "head_to_head": {
    "beat_or_tie": 7,
    "total": 12,
    "rate": 0.583,
    "pass": false
  }
}
```
