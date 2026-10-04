# Benchmark: lidar_flat (lidar tier)

| Item | Ours id | Truth | Ours [90 % interval] | Error | Inside | Gate | Pass | App | App error | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| room_1.ceiling | room_4.ceiling | 2.890 | 2.902 [2.884, 2.920] | +1.2 cm | yes | <= 1.5 cm | ✓ | 3.000 | +11.0 cm | win |
| room_1.dim0 | room_4.wall_0 | 3.350 | 3.343 [3.284, 3.402] | -0.7 cm | yes | <= 1 cm or 0.5 % (stand-in) | ✓ | 3.400 | +5.0 cm | win |
| room_1.dim1 | room_4.wall_5 | 3.010 | 3.166 [3.009, 3.324] | +15.6 cm | yes | <= 1 cm or 0.5 % (stand-in) | ✗ | 3.100 | +9.0 cm | loss |
| room_2.ceiling | room_2.ceiling | 2.940 | 2.942 [2.925, 2.960] | +0.2 cm | yes | <= 1.5 cm | ✓ | 3.000 | +6.0 cm | win |
| room_2.dim0 | room_2.wall_0 | 3.750 | 3.797 [3.735, 3.860] | +4.7 cm | yes | <= 1 cm or 0.5 % (stand-in) | ✗ | 3.900 | +15.0 cm | win |
| room_2.dim1 | room_2.wall_5 | 3.010 | 3.025 [2.868, 3.183] | +1.5 cm | yes | <= 1 cm or 0.5 % (stand-in) | ✓ | 3.700 | +69.0 cm | win |
| room_3.ceiling | room_3.ceiling | 3.000 | 2.932 [2.914, 2.950] | -6.8 cm | no | info (approximate truth) | — |  |  |  |
| room_3.dim0 | room_3.wall_6 | 3.890 | 3.887 [3.726, 4.047] | -0.3 cm | yes | <= 1 cm or 0.5 % (stand-in) | ✓ | 4.100 | +21.0 cm | win |
| room_3.dim1 | room_3.wall_5 | 3.030 | 3.015 [2.857, 3.172] | -1.5 cm | yes | <= 1 cm or 0.5 % (stand-in) | ✗ | 3.000 | -3.0 cm | win |
| kitchen.ceiling | room_5.ceiling | 2.900 | 2.910 [2.892, 2.928] | +1.0 cm | yes | <= 1.5 cm | ✓ | 3.000 | +10.0 cm | win |
| kitchen.dim0 | room_5.wall_6 | 3.000 | 2.996 [2.940, 3.052] | -0.4 cm | yes | <= 1 cm or 0.5 % (stand-in) | ✓ | 3.000 | +0.0 cm | tie |
| kitchen.dim1 | room_5.wall_7 | 2.500 | 2.545 [2.389, 2.700] | +4.5 cm | yes | info (approximate truth) | — |  |  |  |
| hall.area | room_1.floor_area | 36.000 | 36.600 [35.932, 37.268] | +0.60 m2 | yes | info (no area gate in the brief) | — | 34.600 | -1.40 m2 | win |
| hall.ceiling | room_1.ceiling | 2.500 | 2.482 [2.464, 2.500] | -1.8 cm | yes | <= 1.5 cm | ✗ | 2.600 | +10.0 cm | win |
| hall.dim0 | room_1.wall_14 | 8.500 | 8.425 [8.193, 8.658] | -7.5 cm | yes | info (approximate truth) | — |  |  |  |
| hall.dim1 | room_1.wall_17 | 3.500 | 3.250 [3.034, 3.466] | -25.0 cm | no | info (approximate truth) | — |  |  |  |

## Summary

```
{
  "wall": {
    "passed": 4,
    "total": 7,
    "rate": 0.571
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
  "interval_coverage": 1.0,
  "median_abs_error_m": {
    "wall": 0.015,
    "ceiling": 0.0107,
    "area": 0.6
  },
  "median_abs_error_pct": {
    "wall": 0.5,
    "ceiling": 0.37,
    "area": 1.67
  },
  "head_to_head": {
    "beat_or_tie": 11,
    "total": 12,
    "rate": 0.917,
    "pass": true
  }
}
```
