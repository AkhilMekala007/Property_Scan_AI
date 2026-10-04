# Benchmark: lidar_flat (lidar tier)

| Item | Ours id | Truth | Ours [90 % interval] | Error | Inside | Gate | Pass | App | App error | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| room_1.ceiling | room_4.ceiling | 2.890 | 2.891 [2.874, 2.907] | +0.1 cm | yes | <= 1.5 cm | ✓ | 3.000 | +11.0 cm | win |
| room_1.dim0 | room_4.wall_0 | 3.350 | 3.349 [3.317, 3.382] | -0.1 cm | yes | <= 1 cm or 0.5 % (stand-in) | ✓ | 3.400 | +5.0 cm | win |
| room_1.dim1 | room_4.wall_1 | 3.010 | 3.075 [2.954, 3.196] | +6.5 cm | yes | <= 1 cm or 0.5 % (stand-in) | ✗ | 3.100 | +9.0 cm | win |
| room_2.ceiling | room_2.ceiling | 2.940 | 2.921 [2.905, 2.938] | -1.8 cm | no | <= 1.5 cm | ✗ | 3.000 | +6.0 cm | win |
| room_2.dim0 | room_2.wall_10 | 3.750 | 3.726 [3.637, 3.815] | -2.4 cm | yes | <= 1 cm or 0.5 % (stand-in) | ✗ | 3.900 | +15.0 cm | win |
| room_2.dim1 | room_2.wall_11 | 3.010 | 3.218 [3.129, 3.306] | +20.8 cm | no | <= 1 cm or 0.5 % (stand-in) | ✗ | 3.700 | +69.0 cm | win |
| room_3.ceiling | room_3.ceiling | 3.000 | 2.932 [2.915, 2.948] | -6.8 cm | no | info (approximate truth) | — |  |  |  |
| room_3.dim0 | room_3.wall_8 | 3.890 | 4.403 [4.312, 4.494] | +51.3 cm | no | <= 1 cm or 0.5 % (stand-in) | ✗ | 4.100 | +21.0 cm | loss |
| room_3.dim1 | room_3.wall_7 | 3.030 | 3.083 [3.051, 3.114] | +5.2 cm | no | <= 1 cm or 0.5 % (stand-in) | ✗ | 3.000 | -3.0 cm | loss |
| kitchen.ceiling | room_5.ceiling | 2.900 | 2.911 [2.894, 2.927] | +1.1 cm | yes | <= 1.5 cm | ✓ | 3.000 | +10.0 cm | win |
| kitchen.dim0 | room_5.wall_4 | 3.000 | 3.002 [2.970, 3.033] | +0.2 cm | yes | <= 1 cm or 0.5 % (stand-in) | ✓ | 3.000 | +0.0 cm | tie |
| kitchen.dim1 | room_5.wall_5 | 2.500 | 2.412 [2.383, 2.440] | -8.8 cm | no | info (approximate truth) | — |  |  |  |
| hall.area | room_1.floor_area | 36.000 | 35.700 [35.296, 36.104] | -0.30 m2 | yes | info (no area gate in the brief) | — | 34.600 | -1.40 m2 | win |
| hall.ceiling | room_1.ceiling | 2.500 | 2.488 [2.472, 2.505] | -1.2 cm | yes | <= 1.5 cm | ✓ | 2.600 | +10.0 cm | win |
| hall.dim0 | room_1.wall_12 | 8.500 | 7.350 [7.222, 7.478] | -115.0 cm | no | info (approximate truth) | — |  |  |  |
| hall.dim1 | room_1.wall_15 | 3.500 | 3.163 [3.074, 3.251] | -33.7 cm | no | info (approximate truth) | — |  |  |  |

## Summary

```
{
  "wall": {
    "passed": 2,
    "total": 7,
    "rate": 0.286
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
  "interval_coverage": 0.636,
  "median_abs_error_m": {
    "wall": 0.0525,
    "ceiling": 0.0113,
    "area": 0.3
  },
  "median_abs_error_pct": {
    "wall": 1.73,
    "ceiling": 0.42,
    "area": 0.83
  },
  "head_to_head": {
    "beat_or_tie": 10,
    "total": 12,
    "rate": 0.833,
    "pass": true
  }
}
```
