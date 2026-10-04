# Benchmark: photos_v2 (photo tier)

| Item | Ours id | Truth | Ours [90 % interval] | Error | Inside | Gate | Pass | App | App error | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| room_1.ceiling | room_1.ceiling | 2.890 | 2.786 [2.481, 3.092] | -10.4 cm | yes | +-8 % (stand-in) | ✓ | 3.000 | +11.0 cm | win |
| room_1.dim0 | room_1.wall_9 | 3.350 | 3.446 [3.121, 3.771] | +9.6 cm | yes | +-8 % | ✓ | 3.400 | +5.0 cm | loss |
| room_1.dim1 | room_1.wall_8 | 3.010 | 3.115 [2.800, 3.430] | +10.5 cm | yes | +-8 % | ✓ | 3.100 | +9.0 cm | loss |
| room_2.ceiling | room_2.ceiling | 2.940 | 2.656 [2.351, 2.962] | -28.4 cm | yes | +-8 % (stand-in) | ✗ | 3.000 | +6.0 cm | loss |
| room_2.dim0 | room_2.wall_13 | 3.750 | 3.347 [3.018, 3.676] | -40.3 cm | no | +-8 % | ✗ | 3.900 | +15.0 cm | loss |
| room_2.dim1 | room_2.wall_2 | 3.010 | 2.675 [2.377, 2.973] | -33.5 cm | no | +-8 % | ✗ | 3.700 | +69.0 cm | win |
| room_3.ceiling | room_3.ceiling | 3.000 | 2.752 [2.447, 3.058] | -24.8 cm | yes | info (approximate truth) | — |  |  |  |
| room_3.dim0 | room_3.wall_7 | 3.890 | 3.948 [3.580, 4.316] | +5.8 cm | yes | +-8 % | ✓ | 4.100 | +21.0 cm | win |
| room_3.dim1 | room_3.wall_10 | 3.030 | 3.012 [2.704, 3.320] | -1.8 cm | yes | +-8 % | ✓ | 3.000 | -3.0 cm | win |
| kitchen.ceiling | kitchen.ceiling | 2.900 | 2.719 [2.414, 3.024] | -18.1 cm | yes | +-8 % (stand-in) | ✓ | 3.000 | +10.0 cm | loss |
| kitchen.dim0 | kitchen.wall_0 | 3.000 | 2.937 [2.645, 3.229] | -6.3 cm | yes | +-8 % | ✓ | 3.000 | +0.0 cm | loss |
| kitchen.dim1 | kitchen.wall_9 | 2.500 | 1.675 [1.436, 1.914] | -82.5 cm | no | info (approximate truth) | — |  |  |  |
| hall.area | hall.floor_area | 36.000 | 42.817 [39.686, 45.948] | +6.82 m2 | no | info (no area gate in the brief) | — | 34.600 | -1.40 m2 | loss |
| hall.ceiling | hall.ceiling | 2.500 | 2.724 [2.419, 3.030] | +22.4 cm | yes | +-8 % (stand-in) | ✗ | 2.600 | +10.0 cm | loss |
| hall.dim0 | hall.wall_6 | 8.500 | 9.275 [8.557, 9.993] | +77.5 cm | no | info (approximate truth) | — |  |  |  |
| hall.dim1 | hall.wall_1 | 3.500 | 2.325 [2.048, 2.602] | -117.5 cm | no | info (approximate truth) | — |  |  |  |

## Summary

```
{
  "wall": {
    "passed": 5,
    "total": 7,
    "rate": 0.714
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
  "missed": 0,
  "interval_coverage": 0.818,
  "median_abs_error_m": {
    "wall": 0.096,
    "ceiling": 0.2026,
    "area": 6.817
  },
  "median_abs_error_pct": {
    "wall": 2.87,
    "ceiling": 7.6,
    "area": 18.94
  },
  "head_to_head": {
    "beat_or_tie": 4,
    "total": 12,
    "rate": 0.333,
    "pass": false
  }
}
```
