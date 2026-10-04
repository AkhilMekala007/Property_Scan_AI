# Capture protocol (one page)

Follow these steps exactly. They work on any **iPhone 15 or newer**. The LiDAR scan needs a **Pro** model (15 Pro, 16 Pro, 17 Pro, or a Pro Max).

## Before you start (2 min)

- Turn **on all the lights**, and open the curtains and blinds.
- Open **all interior doors fully**.
- Ask people and pets to **stay out of the picture**.
- Charge the phone above 50 % and free up 2 GB of storage.
- **Video and photos only:** go to Settings → Camera → **Formats → Most Compatible**, then Settings → Camera → **Record Video → 1080p at 30 fps**.

## Choose the tier

| You have | Capture | App |
|---|---|---|
| A Pro iPhone | **LiDAR scan** (most accurate) | **Stray Scanner** (free, App Store) |
| Any iPhone 15 or newer | **Video walkthrough** | the built-in **Camera**, Video mode |
| Any iPhone 15 or newer | **Photos**, 6–8 per room | the built-in **Camera**, Photo mode |

## Video and LiDAR: how to walk

1. **Hold the phone sideways** (landscape), at **chest height**, using the normal **1×** camera (not 0.5×).
2. Start **just inside the entrance**.
3. Walk **slowly** (one step per second) **through the middle of each room**, not along the walls. Stay **2 m or more** from the wall you are filming.
4. Tilt the phone **slightly down**, so the line where the **floor meets the wall** is always in the lower part of the picture.
5. In **every room**, turn slowly all the way round (about 5 seconds per quarter turn). Then tilt up to the **ceiling** for 5 seconds and down to the **floor** for 3 seconds.
6. **Walk through every doorway** and pause there for 2 seconds.
7. **Finish where you started.**

**Don't:** run, spin quickly, film people, stand close to walls or mirrors, or point at a bright window for long.

A 3-bedroom flat takes **4–6 minutes**.

**Using Stray Scanner:** open the app, tap record, walk as above, then tap stop. The scan saves automatically.

## Photos: how to shoot

For **each room**, take **6–8 photos**, and never more than 8.

1. **Hold the phone sideways** (landscape), at chest height, using the normal **1×** camera (not 0.5×).
2. Stand **in a corner** with your back to the walls, and photograph **across the room**. Do this from **each corner and from the doorway**.
3. **In every photo, check** that you can see the **floor along the far wall** and the **ceiling line**. If you only see wall, step back.
4. Take **one photo of the room's door**, facing it straight on from inside the room, 2–3 m back. The whole door frame, from the floor to above its top, must be in the picture.
   - In the hall, do the same for each doorway, taken from the hall side.
5. **Stay inside the room.** Don't photograph through a doorway into the next room.

Put each room's photos in **its own folder**, named after the room (for example `Hall`, `Kitchen`, `Room_1`). Put all the room folders inside **one folder** for the property.

## Hand the files over

- **Stray Scanner (LiDAR):** connect the iPhone to the laptop with a USB cable. Open **Apple Devices** (Windows) or **Finder** (Mac), go to *Files* → **Stray Scanner**, and drag the scan folder out.
- **Video:** copy the **single video file** into an empty folder, using AirDrop, USB, or Google Drive.
- **Photos:** copy the property folder, with its room folders inside it, in the same way.

## Run

```bash
scan run <folder> --device "iPhone 15"
```

There is one command for every tier; the tier is detected from the files. The results are written to `outputs/<folder name>/`:

- `result.json`: all the measurements, each with its interval
- `plan.png`: the floor plan
- `qc_report.md`: any capture problems, and how to fix them on the next capture
