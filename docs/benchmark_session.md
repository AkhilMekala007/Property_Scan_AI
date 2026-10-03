# Benchmark session checklist (3BHK)

One session produces everything the benchmark, the head-to-head (Part 3), calibration and the fix loop need. Allow **2.5–3 hours**. Do the steps in this order.

## Bring

- [ ] Friend's **iPhone 17 Pro**, charged, with **Stray Scanner** and **Polycam** installed
- [ ] Your **iPhone 15**, charged, 5 GB free
- [ ] **Laser distance meter** (strongly preferred; ±2 mm class) or a 5 m tape
- [ ] Paper + pen for a sketch, or this file printed
- [ ] Staged damage: a sheet of paper with a **brown tea/coffee stain** (≈ 20–40 cm across, dried) and **masking tape** with a thin dark **crack** line drawn on it (≈ 50–120 cm long)
- [ ] Removable tape to stick both on

## 0. Prepare (10 min)

1. Pick the **damage room** (a bedroom). Stick the stain on the **ceiling** or high on a wall, and the crack tape on a **different wall**. Measure both (stain width × height, crack length) and note where they are.
2. Pick **two rooms for Polycam** (the damage room and one other bedroom).
3. Pick the **repeat room** (any bedroom) — it will be scanned twice.
4. Follow the "Before you start" list in `capture_protocol.md` (lights, doors, people).

## 1. Sketch and measure (45–60 min) — do this first, before scanning

Draw each room from above. Label the room (`bedroom1`, `bedroom2`, `bedroom3`, `hall`, `kitchen`, `passage` …). In each room, label the walls **A, B, C, D …** going **clockwise**, starting with the wall that has the **room's entrance door**. Mark doors and windows on the walls.

Measure with these rules (they match how the pipeline measures):

| What | How |
|---|---|
| **Wall length** | Inside the room, from one corner to the other, **at about 1 m height**, wall face to wall face (ignore skirting boards). If furniture blocks it, measure at 1.5 m. |
| **Ceiling height** | Laser on the **floor in the middle of the room**, pointing straight up. |
| **Door width** | The **clear opening**: narrowest point between the two sides of the frame, at about 1 m height. Door fully open. |
| **Window width** | Width of the **opening in the wall** (inside edge to inside edge of the wall recess), at mid height — not the glass. |
| **Window sill** | Floor to the bottom of the wall opening. |
| **Repeat room** | Measure it **twice** (walls and ceiling), independently. |

Either fill `data/raw/benchmark/ground_truth.yaml` (copy `bench/ground_truth/TEMPLATE.yaml`; leave `mapping:` empty) or write the numbers on the sketch and photograph it (`sketch.jpg`). Wall labels in the sketch:

```
              C
       ┌─────────────┐
     B │  bedroom1   │ D        A = wall with the room's entrance door,
       └───[door]────┘          then B, C, D clockwise (seen from above)
              A
```

## 2. LiDAR scans with the 17 Pro (15 min)

1. **Whole flat**, Stray Scanner, following `capture_protocol.md` exactly. Name: `lidar_flat`.
2. **Repeat room, scan 1** — just that room, start and end in its doorway. Name: `lidar_repeat_1`.
3. **Repeat room, scan 2** — again, separately. Name: `lidar_repeat_2`.
4. **Damage room** close-up pass — 1 minute, include the stain and the crack from 1–2 m. Name: `lidar_damage`.

## 3. Polycam with the 17 Pro (15 min)

Polycam makes **its own live scan** — it does not use the Stray Scanner recordings. For each of the two chosen rooms: Polycam → **Room** mode (LiDAR) → scan that room → save (3–5 min per room).

Then, into `data/raw/benchmark/polycam_<room>/`:
1. **Export** whatever the free tier allows (floor plan image / PDF with dimensions, or a 3D file).
2. **Screenshots** of Polycam's measurement screens (room dimensions, ceiling height, door widths) — these still give its numbers if the free export is limited.
3. The **Polycam app version** (Settings → About): write it on the sketch or in the `competitor:` section of the ground-truth file.

## 4. Video with your iPhone 15 (10 min)

First set Settings → Camera → Formats → **Most Compatible** and Record Video → **1080p at 30 fps** (also makes the photos JPEG instead of HEIC).
**Whole flat**, Camera app, video mode, following `capture_protocol.md`. Name: `video_flat`.
Optional second video of the repeat room: `video_repeat`.

## 5. Photos with your iPhone 15 (20 min)

Every room, 2–8 photos, following `capture_protocol.md`. One folder per room, all inside `photos_flat/`.

## 6. Low light (optional, 10 min)

Repeat the LiDAR scan of one room with only one lamp on: `lidar_lowlight`. Covers the brief's "low light" requirement.

## 7. Hand over

Copy everything into `data/raw/benchmark/` on the laptop (it is ignored by git; the raw data is published separately):

```
data/raw/benchmark/
  lidar_flat/  lidar_repeat_1/  lidar_repeat_2/  lidar_damage/  lidar_lowlight/
  video_flat/IMG_xxxx.MOV       photos_flat/bedroom1/ … hall/ …
  polycam_bedroom1/  polycam_bedroom2/
  sketch.jpg       ground_truth.yaml (or the sketch with numbers)
```
