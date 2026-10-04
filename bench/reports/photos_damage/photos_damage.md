# Benchmark: photos_damage (photo tier)

| Item | Ours id | Truth | Ours [90 % interval] | Error | Inside | Gate | Pass | App | App error | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| room_1.ceiling | room_1.ceiling | 2.890 | 2.905 [2.600, 3.210] | +1.5 cm | yes | +-8 % (stand-in) | ✓ | 3.000 | +11.0 cm | win |
| room_1.dim0 | room_1.wall_0 | 3.350 | 3.512 [3.182, 3.842] | +16.2 cm | yes | +-8 % | ✓ | 3.400 | +5.0 cm | loss |
| room_1.dim1 | room_1.wall_1 | 3.010 | 2.978 [2.672, 3.284] | -3.2 cm | yes | +-8 % | ✓ | 3.100 | +9.0 cm | win |
| room_2.ceiling | — | 2.940 | — | missed |  | +-8 % (stand-in) | ✗ |  |  |  |
| room_2.dim0 | — | 3.750 | — | missed |  | +-8 % | ✗ |  |  |  |
| room_2.dim1 | — | 3.010 | — | missed |  | +-8 % | ✗ |  |  |  |
| room_3.ceiling | — | 3.000 | — | missed |  | info (approximate truth) | ✗ |  |  |  |
| room_3.dim0 | — | 3.890 | — | missed |  | +-8 % | ✗ |  |  |  |
| room_3.dim1 | — | 3.030 | — | missed |  | +-8 % | ✗ |  |  |  |
| kitchen.ceiling | — | 2.900 | — | missed |  | +-8 % (stand-in) | ✗ |  |  |  |
| kitchen.dim0 | — | 3.000 | — | missed |  | +-8 % | ✗ |  |  |  |
| kitchen.dim1 | — | 2.500 | — | missed |  | info (approximate truth) | ✗ |  |  |  |
| hall.area | — | 36.000 | — | missed |  | info (no area gate in the brief) | — |  |  |  |
| hall.ceiling | — | 2.500 | — | missed |  | +-8 % (stand-in) | ✗ |  |  |  |
| hall.dim0 | — | 8.500 | — | missed |  | info (approximate truth) | ✗ |  |  |  |
| hall.dim1 | — | 3.500 | — | missed |  | info (approximate truth) | ✗ |  |  |  |
| room_1.damage0.water_stain.detected | — | 1.000 | 0.000 | not detected |  | info (detection + extent; no damage gate in the brief) | — |  |  |  |

## Summary

```
{
  "wall": {
    "passed": 2,
    "total": 10,
    "rate": 0.2
  },
  "ceiling": {
    "passed": 1,
    "total": 5,
    "rate": 0.2
  },
  "opening": {
    "passed": 0,
    "total": 0,
    "rate": null
  },
  "missed": 13,
  "interval_coverage": 1.0,
  "median_abs_error_m": {
    "wall": 0.0971,
    "ceiling": 0.015
  },
  "median_abs_error_pct": {
    "wall": 2.95,
    "ceiling": 0.52
  },
  "head_to_head": {
    "beat_or_tie": 2,
    "total": 3,
    "rate": 0.667,
    "pass": false
  }
}
```
