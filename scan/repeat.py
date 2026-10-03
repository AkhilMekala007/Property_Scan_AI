"""Repeatability harness: how much do outputs move when the input poses move by a few mm?

The brief's repeatability gate asks that two captures of the same room agree within
1 cm or 0.5 % per wall. A real second capture differs in noise and poses; here we
emulate the pose part with tiny per-fragment perturbations (the size of what drift
correction changes) and measure how much each output moves. Anything that jumps
under sub-centimetre noise will fail the real gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np

from scan.core.types import FrameSet
from scan.drift import _correct_trajectory, _yaw_only

FRAGMENT = 20


def perturb(frameset: FrameSet, seed: int, trans_sigma_m: float = 0.005, yaw_sigma_deg: float = 0.1) -> FrameSet:
    """Apply a small random yaw + translation to each fragment of keyframes (and the trajectory)."""
    rng = np.random.default_rng(seed)
    frames = list(frameset.frames)
    fragments, corrections = [], []
    for start in range(0, len(frames), FRAGMENT):
        frag = frames[start:start + FRAGMENT]
        yaw = np.radians(rng.normal(0, yaw_sigma_deg))
        c, s = np.cos(yaw), np.sin(yaw)
        T = np.eye(4)
        T[:3, :3] = [[c, 0, s], [0, 1, 0], [-s, 0, c]]
        T[:3, 3] = rng.normal(0, trans_sigma_m, 3) * [1, 0.2, 1]
        T = _yaw_only(T)
        fragments.append(frag)
        corrections.append(T)
        for k, f in enumerate(frag):
            if f.T_world_cam is not None:
                frames[start + k] = replace(f, T_world_cam=T @ f.T_world_cam)
    traj = _correct_trajectory(frameset.trajectory, fragments, corrections)
    return replace(frameset, frames=frames, trajectory=traj)


@dataclass
class RunSummary:
    rooms: list[dict]  # centroid, area, ceiling, real walls [(axis, coord, mid, length)]
    openings: list[dict]  # centre, width, kind
    n_rooms: int = 0
    n_openings: int = 0


def summarise(result) -> RunSummary:
    rooms = []
    for r in result.plan.rooms:
        pts = np.array(r.corners_xz)
        walls = []
        for k, w in enumerate(r.walls):
            if w.inferred:
                continue
            a, b = pts[k], pts[(k + 1) % len(pts)]
            walls.append({"mid": (a + b) / 2, "dir": (b - a) / (np.linalg.norm(b - a) + 1e-12), "length": w.length_m})
        rooms.append({"centroid": pts.mean(axis=0), "area": r.floor_area_m2, "ceiling": r.ceiling_height_m,
                      "walls": walls, "name": r.name})
    openings = [{"centre": np.array(o.center_xz), "width": o.width_m, "kind": o.kind} for o in result.plan.openings]
    return RunSummary(rooms, openings, len(rooms), len(openings))


@dataclass
class RepeatReport:
    runs: int
    room_counts: list[int]
    opening_counts: list[int]
    room_area_spread_m2: list[float] = field(default_factory=list)  # per room matched in every run
    room_area_spread_pct: list[float] = field(default_factory=list)
    wall_length_spread_m: list[float] = field(default_factory=list)  # per observed wall matched in every run
    ceiling_spread_m: list[float] = field(default_factory=list)
    opening_width_spread_m: list[float] = field(default_factory=list)
    unmatched_rooms: int = 0
    unmatched_walls: int = 0

    def headline(self) -> dict:
        def stat(v, q):
            return round(float(np.percentile(v, q)), 4) if v else None

        walls = np.array(self.wall_length_spread_m)
        return {
            "room_counts": self.room_counts,
            "opening_counts": self.opening_counts,
            "rooms_stable": len(set(self.room_counts)) == 1,
            "room_area_spread_pct_max": stat(self.room_area_spread_pct, 100),
            "wall_spread_m_median": stat(self.wall_length_spread_m, 50),
            "wall_spread_m_p90": stat(self.wall_length_spread_m, 90),
            "walls_within_1cm": round(float((walls <= 0.01).mean()), 3) if len(walls) else None,
            "ceiling_spread_m_max": stat(self.ceiling_spread_m, 100),
            "opening_width_spread_m_median": stat(self.opening_width_spread_m, 50),
            "unmatched_walls": self.unmatched_walls,
        }


def compare(runs: list[RunSummary], room_match_m: float = 0.8, wall_match_m: float = 0.15,
            opening_match_m: float = 0.3) -> RepeatReport:
    """Match rooms / walls / openings of every run to the first run and record their spread."""
    rep = RepeatReport(len(runs), [r.n_rooms for r in runs], [r.n_openings for r in runs])
    ref = runs[0]
    for room in ref.rooms:
        matches = [room]
        for other in runs[1:]:
            d = [np.linalg.norm(o["centroid"] - room["centroid"]) for o in other.rooms]
            if not d or min(d) > room_match_m:
                break
            matches.append(other.rooms[int(np.argmin(d))])
        if len(matches) < len(runs):
            rep.unmatched_rooms += 1
            continue
        areas = [m["area"] for m in matches]
        rep.room_area_spread_m2.append(max(areas) - min(areas))
        rep.room_area_spread_pct.append(100 * (max(areas) - min(areas)) / max(np.mean(areas), 1e-6))
        ceilings = [m["ceiling"] for m in matches]
        if all(c is not None for c in ceilings):
            rep.ceiling_spread_m.append(max(ceilings) - min(ceilings))
        for wall in room["walls"]:
            lengths = [wall["length"]]
            for m in matches[1:]:
                cands = [w for w in m["walls"] if abs(float(np.dot(w["dir"], wall["dir"]))) > 0.98
                         and np.linalg.norm(w["mid"] - wall["mid"]) <= max(wall_match_m, 0.25 * wall["length"])]
                if not cands:
                    break
                best = min(cands, key=lambda w: np.linalg.norm(w["mid"] - wall["mid"]))
                lengths.append(best["length"])
            if len(lengths) == len(runs):
                rep.wall_length_spread_m.append(max(lengths) - min(lengths))
            else:
                rep.unmatched_walls += 1
    for op in ref.openings:
        widths = [op["width"]]
        for other in runs[1:]:
            d = [np.linalg.norm(o["centre"] - op["centre"]) for o in other.openings]
            if not d or min(d) > opening_match_m:
                break
            widths.append(other.openings[int(np.argmin(d))]["width"])
        if len(widths) == len(runs):
            rep.opening_width_spread_m.append(max(widths) - min(widths))
    return rep
