# Capture protocol (one page)

Follow these steps exactly. They work for any iPhone 15 or newer; the LiDAR scan needs a **Pro** model (15 Pro, 16 Pro, 17 Pro and Pro Max).

## Before you start (2 min)

- Turn **on all lights**; open curtains and blinds.
- Open **all interior doors fully**, flat against the wall.
- Ask people and pets to stay out of the rooms you are scanning.
- Charge the phone above 50 % and free 2 GB of storage.
- **Video and photos only:** Settings → Camera → **Formats → Most Compatible**, and Settings → Camera → **Record Video → 1080p at 30 fps**.

## Choose the tier

| You have | Capture | App |
|---|---|---|
| A Pro iPhone (has LiDAR) | **LiDAR scan** (most accurate) | **Stray Scanner** (free, App Store) |
| Any iPhone 15 or newer | **Video walkthrough** | Built-in **Camera**, Video mode |
| Any iPhone 15 or newer | **Photos**, 6–8 per room | Built-in **Camera**, Photo mode |

## How to walk (LiDAR scan and video)

1. Start recording **just inside the entrance**. Hold the phone at **chest height**, screen facing you.
2. Walk **slowly** — about **one step per second** — along the walls of each room. Keep **1–2 m** from the wall you are filming.
3. In **every room**: film all walls, **tilt the phone up to the ceiling for 5 seconds**, then **down to the floor for 3 seconds**.
4. **Turn slowly** in corners (about 5 seconds for a quarter turn). Never spin quickly.
5. **Walk through every doorway** (don't just look through it), pausing 2 seconds in the doorway so both sides of the door are seen.
6. Finish **back where you started**, filming the first room again for a few seconds.
7. **Avoid:** standing very close to mirrors or glass, covering the camera, pointing at bright windows for long, running.

Time: about **1 minute per room**. A 3BHK takes 5–8 minutes.

**Stray Scanner:** open the app → tap the record button → walk as above → tap stop. It saves automatically.

## How to take photos (photo tier)

For each room, **6–8 photos** (never more than 8), landscape, phone at chest height, **0.5× (ultra-wide) lens**:

1. **Walk the room, don't stand still.** Take a photo, take **one or two steps sideways** along the wall, turn slightly, take the next. Each photo should **share about half its view** with the previous one. (Photos taken by only turning on one spot give no depth between them.)
2. **Every wall in at least two photos, from two different places**, with the **floor line and the ceiling line** visible.
3. **The door, square-on, from inside the room:** stand facing it, 2–3 m away, the whole door frame from floor to above the top in the photo. Do this in **every** room, including the hall side of each bedroom door. Rooms are joined on the plan by matching these doors.
4. **One room per folder, and stay in it.** Don't photograph through an open doorway into the next space; if the hall and lobby run together, photograph them as two folders split at a natural boundary.
5. Avoid people in frame, mirrors face-on, and photos aimed only at a window.

## Hand the files over

**LiDAR (Stray Scanner):** connect the iPhone to the laptop with a USB cable → open the **Apple Devices** app (Windows) or Finder (Mac) → *Files* → **Stray Scanner** → drag the scan folder out. *Fallback:* in the app, open the scan → share → save to Google Drive → download.

**Video:** AirDrop / USB / Google Drive — copy the **one** video file into an empty folder.

**Photos:** one folder per room, named after the room — `kitchen/`, `bedroom1/`, `hall/` — all inside one folder for the property.

## Run

```bash
scan run <folder> --device "iPhone 17 Pro"
```

One command; the tier is detected from the files. Results: `outputs/<capture>/result.json` and `plan.png`. Total time on the reference laptop: 2–4 minutes for a LiDAR scan.
