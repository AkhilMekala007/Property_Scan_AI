"""C10 Damage: detect, refine, measure on room surfaces, fuse across views."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

from scan.core.types import FrameSet
from scan.damage.detect import DetectConfig, Detection, Detector, run_detection
from scan.damage.measure import MeasureConfig, ViewMeasurement, footprint, project_mask, refine_mask
from scan.measure import RoomMeasurement
from scan.rooms.floormap import GridFrame
from scan.structure import StructureModel

__all__ = ["DamageConfig", "DamageRegion", "DamageResult", "detect_damage"]


@dataclass(frozen=True)
class DamageConfig:
    detect: DetectConfig = field(default_factory=DetectConfig)
    measure: MeasureConfig = field(default_factory=MeasureConfig)
    fuse_dist_m: float = 0.3
    min_views: int = 2
    single_view_score: float = 0.45  # one view is enough only above this score


@dataclass
class DamageRegion:
    id: int
    room_id: int
    room_name: str
    surface: str
    surface_kind: str
    wall_index: int | None
    cls: str
    area_m2: float
    area_sigma_m2: float
    length_m: float
    extent_m: tuple[float, float]
    bottom_m: float | None  # walls: lowest point above the floor
    centroid_2d: tuple[float, float]
    confidence: float
    n_views: int
    outline_2d: list[tuple[float, float]]


@dataclass
class DamageResult:
    regions: list[DamageRegion]
    detections: int
    views_measured: int
    frames_scanned: int
    frames_from_cache: int
    dropped: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _measure_views(frameset: FrameSet, dets: list[Detection], rooms, model, frame2d, cfg: MeasureConfig,
                   dropped: dict) -> list[ViewMeasurement]:
    by_index = {f.index: f for f in frameset.frames}
    views = []
    for d in dets:
        f = by_index.get(d.frame_index)
        if f is None:
            continue
        rgb = f.rgb()
        labels = cv2.resize(f.labels(), (rgb.shape[1], rgb.shape[0]), interpolation=cv2.INTER_NEAREST)
        mask = refine_mask(rgb, labels, d.box, cfg)
        if mask is None or isinstance(mask, str):
            reason = "along_surface_edge" if isinstance(mask, str) else "no_contrast_on_surface"
            dropped[reason] = dropped.get(reason, 0) + 1
            continue
        hit = project_mask(f, mask, rooms, model, frame2d, cfg)
        if hit is None:
            dropped["not_on_a_room_surface"] = dropped.get("not_on_a_room_surface", 0) + 1
            continue
        ref, coords, spacing = hit
        area, sigma, centroid, extent, length, outline, cells, bottom = footprint(coords, spacing)
        views.append(ViewMeasurement(d.frame_index, d.cls, d.score, ref, area, sigma, centroid, extent, length,
                                     bottom if ref.kind == "wall" else None, outline, cells))
    return views


def fuse(views: list[ViewMeasurement], cfg: DamageConfig, dropped: dict) -> list[DamageRegion]:
    groups: list[list[ViewMeasurement]] = []
    for v in sorted(views, key=lambda v: -v.score):
        for g in groups:
            ref = g[0]
            if ref.surface.id == v.surface.id and np.hypot(ref.centroid_2d[0] - v.centroid_2d[0],
                                                           ref.centroid_2d[1] - v.centroid_2d[1]) <= cfg.fuse_dist_m:
                g.append(v)
                break
        else:
            groups.append([v])

    regions = []
    for g in groups:
        frames = {v.frame_index for v in g}
        score = max(v.score for v in g)
        if len(frames) < cfg.min_views and score < cfg.single_view_score:
            dropped["single_weak_view"] = dropped.get("single_weak_view", 0) + 1
            continue
        votes: dict[str, float] = {}
        for v in g:
            votes[v.cls] = votes.get(v.cls, 0.0) + v.score
        cls = max(votes, key=votes.get)
        same = [v for v in g if v.cls == cls]
        areas = np.array([v.area_m2 for v in same])
        best = max(same, key=lambda v: v.score)
        spread = float(np.median(np.abs(areas - np.median(areas))) * 1.4826) if len(areas) > 1 else 0.0
        sigma = max(float(np.median([v.area_sigma_m2 for v in same])), spread)
        agree = votes[cls] / sum(votes.values())
        regions.append(DamageRegion(
            id=len(regions), room_id=best.surface.room_id, room_name=best.surface.room_name,
            surface=best.surface.id, surface_kind=best.surface.kind, wall_index=best.surface.wall_index,
            cls=cls, area_m2=round(float(np.median(areas)), 4), area_sigma_m2=round(sigma, 4),
            length_m=round(float(np.median([v.length_m for v in same])), 3),
            extent_m=tuple(round(float(x), 3) for x in best.extent_2d),
            bottom_m=None if best.bottom_m is None else round(float(np.median([v.bottom_m for v in same])), 3),
            centroid_2d=tuple(round(float(x), 3) for x in best.centroid_2d),
            confidence=round(float(score * agree * min(1.0, len(frames) / 3)), 3),
            n_views=len(frames), outline_2d=best.outline_2d))
    return regions


def detect_damage(frameset: FrameSet, rooms: list[RoomMeasurement], model: StructureModel, frame2d: GridFrame,
                  cfg: DamageConfig | None = None, detector: Detector | None = None,
                  cache_root: Path | None = None) -> DamageResult:
    cfg = cfg or DamageConfig()
    root = cache_root or frameset.cache_dir or Path("outputs") / "cache" / frameset.meta.capture_id
    dets, cached = run_detection(frameset.frames, Path(root), cfg.detect, detector)
    dropped: dict = {}
    views = _measure_views(frameset, dets, rooms, model, frame2d, cfg.measure, dropped)
    regions = fuse(views, cfg, dropped)
    from scan.damage.detect import select_frames

    return DamageResult(regions, len(dets), len(views), len(select_frames(frameset.frames, cfg.detect.max_frames)),
                        cached, dropped)
