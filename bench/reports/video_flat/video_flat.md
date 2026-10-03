# Benchmark: video_flat (video tier)

| Item | Ours id | Truth | Ours [90 % interval] | Error | Inside | Gate | Pass | App | App error | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| room_1.ceiling | — | 2.890 | — | missed |  | +-3 % (stand-in) | ✗ |  |  |  |
| room_1.dim0 | — | 3.350 | — | missed |  | +-3 % | ✗ |  |  |  |
| room_1.dim1 | — | 3.010 | — | missed |  | +-3 % | ✗ |  |  |  |
| room_2.ceiling | — | 2.940 | — | missed |  | +-3 % (stand-in) | ✗ |  |  |  |
| room_2.dim0 | — | 3.750 | — | missed |  | +-3 % | ✗ |  |  |  |
| room_2.dim1 | — | 3.010 | — | missed |  | +-3 % | ✗ |  |  |  |
| room_3.ceiling | — | 3.000 | — | missed |  | info (approximate truth) | ✗ |  |  |  |
| room_3.dim0 | — | 3.890 | — | missed |  | +-3 % | ✗ |  |  |  |
| room_3.dim1 | — | 3.030 | — | missed |  | +-3 % | ✗ |  |  |  |
| kitchen.ceiling | — | 2.900 | — | missed |  | +-3 % (stand-in) | ✗ |  |  |  |
| kitchen.dim0 | — | 3.000 | — | missed |  | +-3 % | ✗ |  |  |  |
| kitchen.dim1 | — | 2.500 | — | missed |  | info (approximate truth) | ✗ |  |  |  |
| hall.area | — | 36.000 | — | missed |  | info (no area gate in the brief) | — |  |  |  |
| hall.ceiling | — | 2.500 | — | missed |  | +-3 % (stand-in) | ✗ |  |  |  |
| hall.dim0 | — | 8.500 | — | missed |  | info (approximate truth) | ✗ |  |  |  |
| hall.dim1 | — | 3.500 | — | missed |  | info (approximate truth) | ✗ |  |  |  |

## Summary

```
{
  "wall": {
    "passed": 0,
    "total": 10,
    "rate": 0.0
  },
  "ceiling": {
    "passed": 0,
    "total": 5,
    "rate": 0.0
  },
  "opening": {
    "passed": 0,
    "total": 0,
    "rate": null
  },
  "missed": 16,
  "interval_coverage": null,
  "median_abs_error_m": {},
  "median_abs_error_pct": {}
}
```
