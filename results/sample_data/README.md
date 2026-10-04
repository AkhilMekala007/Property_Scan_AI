# Results on the provided sample data

The three Stray Scanner captures shared with the assessment, run through the final pipeline with one
command each (cached labels; cold runs take ~4–7 min each on a CPU laptop):

```bash
scan run single_room              --out results/sample_data/single_room
scan run single_scan_floor_only   --out results/sample_data/single_scan_floor_only
scan run single_scan_with_ceiling --out results/sample_data/single_scan_with_ceiling
```

| Capture | Rooms | Net floor area (90 % interval) | Ceilings | Connected / overlaps |
|---|---|---|---|---|
| `single_room` | 3 | 19.36 m² [18.87, 19.86] | not filmed → `null` with reason | yes / 0 |
| `single_scan_floor_only` | 4 | 57.92 m² [57.02, 58.82] | not filmed → `null` with reason | yes / 0 |
| `single_scan_with_ceiling` | 5 | 60.50 m² [59.75, 61.25] | 2.29–3.07 m, all measured | yes / 0 |

Each folder holds `result.json` (validated against `schema/result.schema.json`, an interval on every
number), `plan.png` (the stitched, dimensioned plan) and `qc_report.md` (capture issues with fixes).

No ground truth was provided for these captures, so they are not scored here; accuracy is measured on
our own tape-measured benchmark (`bench/REPORT.md`). The samples were used during development, and the
LiDAR-tier repeatability proxy and drift ablation in `docs/LLD/09_drift.md` and `docs/LLD/10_repeatability.md`
were measured on them.
