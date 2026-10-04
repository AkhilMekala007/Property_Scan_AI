# Benchmark: photos_v6 (photo tier)

| Item | Ours id | Truth | Ours [90 % interval] | Error | Inside | Gate | Pass | App | App error | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| room_1.ceiling | room_1.ceiling | 2.890 | 2.657 [2.351, 2.962] | -23.3 cm | yes | +-8 % (stand-in) | ✗ | 3.000 | +11.0 cm | loss |
| room_1.dim0 | room_1.wall_0 | 3.350 | 3.065 [2.764, 3.365] | -28.5 cm | yes | +-8 % | ✗ | 3.400 | +5.0 cm | loss |
| room_1.dim1 | room_1.wall_7 | 3.010 | 2.807 [2.512, 3.102] | -20.3 cm | yes | +-8 % | ✓ | 3.100 | +9.0 cm | loss |
| room_2.ceiling | — | 2.940 | — | missed |  | +-8 % (stand-in) | ✗ |  |  |  |
| room_2.dim0 | — | 3.750 | — | missed |  | +-8 % | ✗ |  |  |  |
| room_2.dim1 | — | 3.010 | — | missed |  | +-8 % | ✗ |  |  |  |
| room_3.ceiling | room_3.ceiling | 3.000 | 2.723 [2.417, 3.028] | -27.7 cm | yes | info (approximate truth) | — |  |  |  |
| room_3.dim0 | room_3.wall_5 | 3.890 | 4.013 [3.641, 4.385] | +12.3 cm | yes | +-8 % | ✓ | 4.100 | +21.0 cm | win |
| room_3.dim1 | room_3.wall_2 | 3.030 | 2.795 [2.501, 3.090] | -23.5 cm | yes | +-8 % | ✓ | 3.000 | -3.0 cm | loss |
| kitchen.ceiling | kitchen.ceiling | 2.900 | 2.744 [2.438, 3.049] | -15.6 cm | yes | +-8 % (stand-in) | ✓ | 3.000 | +10.0 cm | loss |
| kitchen.dim0 | kitchen.wall_7 | 3.000 | 3.250 [2.917, 3.583] | +25.0 cm | yes | +-8 % | ✗ | 3.000 | +0.0 cm | loss |
| kitchen.dim1 | kitchen.wall_8 | 2.500 | 2.956 [2.651, 3.260] | +45.6 cm | no | info (approximate truth) | — |  |  |  |
| hall.area | hall.floor_area | 36.000 | 15.741 [13.682, 17.800] | -20.26 m2 | no | info (no area gate in the brief) | — | 34.600 | -1.40 m2 | loss |
| hall.ceiling | hall.ceiling | 2.500 | 2.554 [2.248, 2.859] | +5.4 cm | yes | +-8 % (stand-in) | ✓ | 2.600 | +10.0 cm | win |
| hall.dim0 | hall.wall_5 | 8.500 | 3.050 [2.739, 3.360] | -545.0 cm | no | info (approximate truth) | — |  |  |  |
| hall.dim1 | hall.wall_12 | 3.500 | 2.825 [2.518, 3.132] | -67.5 cm | no | info (approximate truth) | — |  |  |  |

## Summary

```
{
  "wall": {
    "passed": 3,
    "total": 7,
    "rate": 0.429
  },
  "ceiling": {
    "passed": 2,
    "total": 4,
    "rate": 0.5
  },
  "opening": {
    "passed": 0,
    "total": 0,
    "rate": null
  },
  "missed": 3,
  "interval_coverage": 1.0,
  "median_abs_error_m": {
    "wall": 0.2347,
    "ceiling": 0.1561,
    "area": 20.259
  },
  "median_abs_error_pct": {
    "wall": 7.75,
    "ceiling": 5.38,
    "area": 56.27
  },
  "head_to_head": {
    "beat_or_tie": 2,
    "total": 9,
    "rate": 0.222,
    "pass": false
  }
}
```
