# Fix declaration (Part 4)

Full log with timestamps, every prediction written before its result: `docs/fix_loop.md`.

## 1. Worst gate and failing number

**Video tier, wall lengths within ±3 %.** On the benchmark walkthrough (`video_flat`, iPhone 15, 168 s),
**0 of 10** tape-measured room dimensions could be matched: the plan came out as 9 partial rooms with 7
overlapping pairs and 50.9 m² net against 78.8 m² (LiDAR) — commit `6ec0cd4`.
(LiDAR walls 4/7, photo walls 7/7 for comparison; `bench/REPORT.md`.)

## 2. Root cause and evidence

Revised twice, each time from measurements:

1. *Initial hypothesis:* chunk joins too weak (8 shared frames). **Evidence against:** stronger joins
   (16 frames + ICP against everything placed, `2fea3a4`) improved every join's overlap fitness
   (e.g. 0.14 → 0.31) but the plan got worse (3 rooms, 31.6 m²).
2. *Second:* fusing depth from chunks at disagreeing scales. **Evidence:** DA3METRIC's scale measured per
   chunk varies 13–33 % along the walk; measuring each chunk alone (`e7b42d1`) kept scales consistent but
   gave 7 partial rooms (a 30 s chunk sees part of a room).
3. **Final diagnosis:** monocular metric scale is only consistent *within* a set of views that DA3 sees
   together, and those views must contain the whole room including floor and ceiling. Long sparse passes
   lose correspondence (one-pass DA3 folded 63 m of walking into 2.3 × 3.4 m); short dense chunks see
   partial rooms; close-to-wall footage (both landscape clips) and portrait orientation (scale spread 2.7×)
   make it worse. The photo tier passes (7/7) precisely because each room's 6–8 views cover the room.

## 3. Fix shipped and predicted number

**Shipped (current default, `scan/fragments.py: video_room_fragments`):** split the video into rooms by
shared views, then reconstruct each room like a photo folder (DA3 on ~8 frames spread over the room's whole
time on screen, scale from 4 of them) — the method that measures photo rooms within 1–3 %. Also ~3× faster
(283 s clip: 25 min vs 81 min).

**Predictions** (recorded before each result): attempt 1 — 5 ± 1 rooms, 67–91 m² (**got 3, 31.6 m²**);
attempt 2 — ≥ 4 rooms, 55–90 m² (**7 partial, 57.9 m²**); attempt 3 — 4–6 rooms (**1 room, 3.8 m²**);
room mode on `video_flat` — gate stays failing, 3–6 mostly partial rooms (**5 partial rooms; held**).

## 4. Outcome

The gate **did not move to pass**. Same clip, before → after: overlapping room pairs 7 → 0, net area 50.9 → 34.1 m², matched dimensions 0 → 0; ceilings ~18 % low where seen (a systematic scale bias). On the best capture (`video_v3`, through room centres), room mode
measured Room_1 at −9 % / −39 % with ceilings ~18 % low; other bedrooms gave no room. The video tier ships
with this documented failure: valid JSON, wide uncalibrated intervals, QC warnings naming the cause
(portrait, scale spread, rooms not measured). The structural fix the diagnosis points to is a scale
reference shared across the walkthrough — the phone's own motion tracking (absent from a plain video file)
or a multi-view model with long-range memory.

## Regenerate before / after

```bash
git checkout 6ec0cd4 && scan run data/raw/benchmark/video_flat --device "iPhone 15"     # before
git checkout <final> && scan run data/raw/benchmark/video_flat --device "iPhone 15"      # after (room mode)
scan bench outputs/video_flat/result.json bench/ground_truth/flat_3bhk.yaml --capture video_flat
```
Readable diff: `git diff 6ec0cd4 <final> -- scan/fragments.py scan/multiview.py`.
