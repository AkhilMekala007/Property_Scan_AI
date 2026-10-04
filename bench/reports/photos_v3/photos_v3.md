# Benchmark: photos_v3 (photo tier)

| Item | Ours id | Truth | Ours [90 % interval] | Error | Inside | Gate | Pass | App | App error | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| room_1.ceiling | — | 2.890 | — | missed |  | +-8 % (stand-in) | ✗ |  |  |  |
| room_1.dim0 | — | 3.350 | — | missed |  | +-8 % | ✗ |  |  |  |
| room_1.dim1 | — | 3.010 | — | missed |  | +-8 % | ✗ |  |  |  |
| room_2.ceiling | — | 2.940 | — | missed |  | +-8 % (stand-in) | ✗ |  |  |  |
| room_2.dim0 | — | 3.750 | — | missed |  | +-8 % | ✗ |  |  |  |
| room_2.dim1 | — | 3.010 | — | missed |  | +-8 % | ✗ |  |  |  |
| room_3.ceiling | room_3.ceiling | 3.000 | 2.752 [2.447, 3.058] | -24.8 cm | yes | info (approximate truth) | — |  |  |  |
| room_3.dim0 | room_3.wall_7 | 3.890 | 3.948 [3.580, 4.316] | +5.8 cm | yes | +-8 % | ✓ | 4.100 | +21.0 cm | win |
| room_3.dim1 | room_3.wall_10 | 3.030 | 3.012 [2.704, 3.320] | -1.8 cm | yes | +-8 % | ✓ | 3.000 | -3.0 cm | win |
| kitchen.ceiling | kitchen.ceiling | 2.900 | — | missed |  | +-8 % (stand-in) | ✗ |  |  |  |
| kitchen.dim0 | kitchen.wall_3 | 3.000 | 1.691 [1.465, 1.916] | -130.9 cm | no | +-8 % | ✗ | 3.000 | +0.0 cm | loss |
| kitchen.dim1 | kitchen.wall_0 | 2.500 | 1.069 [0.900, 1.238] | -143.1 cm | no | info (approximate truth) | — |  |  |  |
| hall.area | hall.floor_area | 36.000 | 10.725 [9.151, 12.299] | -25.27 m2 | no | info (no area gate in the brief) | — | 34.600 | -1.40 m2 | loss |
| hall.ceiling | hall.ceiling | 2.500 | 2.715 [2.409, 3.020] | +21.5 cm | yes | +-8 % (stand-in) | ✗ | 2.600 | +10.0 cm | loss |
| hall.dim0 | hall.wall_8 | 8.500 | 3.675 [3.315, 4.035] | -482.5 cm | no | info (approximate truth) | — |  |  |  |
| hall.dim1 | hall.wall_7 | 3.500 | 1.692 [1.466, 1.917] | -180.8 cm | no | info (approximate truth) | — |  |  |  |

## Summary

```
{
  "wall": {
    "passed": 2,
    "total": 7,
    "rate": 0.286
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
  "missed": 7,
  "interval_coverage": 0.75,
  "median_abs_error_m": {
    "wall": 0.0582,
    "ceiling": 0.2148,
    "area": 25.275
  },
  "median_abs_error_pct": {
    "wall": 1.5,
    "ceiling": 8.59,
    "area": 70.21
  },
  "head_to_head": {
    "beat_or_tie": 2,
    "total": 5,
    "rate": 0.4,
    "pass": false
  }
}
```
