"""C4 Semantics: label every pixel of the selected keyframes as wall / floor / ceiling / ..."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import cv2
import numpy as np

from scan.core.types import Frame, FrameSet
from scan.qc.report import INFO, WARNING, Issue
from scan.semantics.check import geometry_agreement
from scan.semantics.classes import MAPPING_VERSION, Surface, colourise
from scan.semantics.segmenter import Segmenter
from scan.semantics.upright import from_upright, to_upright, upright_quarter_turns

__all__ = ["SemanticsConfig", "SemanticsResult", "SemanticSummary", "Surface", "run_semantics", "write_overlays"]

MIRROR_FRAME_SHARE = 0.01  # a frame "shows a mirror" above this share of pixels
LOW_AGREEMENT = 0.6


@dataclass(frozen=True)
class SemanticsConfig:
    model_key: str = "segformer-b2-ade"
    short_side: int = 320  # model input size; 320 keeps ~90% agreement with 512 at ~4x speed
    max_frames: int = 100
    label_size: tuple[int, int] = (480, 360)  # (w, h) in sensor orientation


@dataclass
class SemanticSummary:
    capture_id: str
    model: str
    n_frames_labelled: int
    n_frames_from_cache: int
    class_shares: dict[str, float]
    mirror_frames: int
    agreement: dict[str, dict[str, float]]
    issues: list[Issue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SemanticsResult:
    frameset: FrameSet  # all input frames; the labelled ones have labels()
    summary: SemanticSummary


def select_frames(frames: list[Frame], max_frames: int) -> list[int]:
    """Positions of frames to label: sharp images only, evenly spread over the walk."""
    candidates = [i for i, f in enumerate(frames) if f.rgb_ok]
    if len(candidates) <= max_frames:
        return candidates
    picks = np.linspace(0, len(candidates) - 1, max_frames).round().astype(int)
    return [candidates[j] for j in np.unique(picks)]


class _LabelCache:
    def __init__(self, root: Path, model_name: str, label_size: tuple[int, int]):
        safe = model_name.replace("/", "_").replace("@", "_")
        self.dir = root / "labels" / safe
        self.manifest = {"model": model_name, "mapping_version": MAPPING_VERSION, "label_size": list(label_size)}
        manifest_path = self.dir / "manifest.json"
        if manifest_path.exists():
            try:
                current = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                current = None
            if current != self.manifest:
                shutil.rmtree(self.dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")

    def paths(self, index: int) -> tuple[Path, Path]:
        return self.dir / f"{index:06d}.png", self.dir / f"{index:06d}_conf.png"

    def has(self, index: int) -> bool:
        return all(p.exists() for p in self.paths(index))

    def save(self, index: int, labels: np.ndarray, conf: np.ndarray) -> None:
        lab_path, conf_path = self.paths(index)
        cv2.imwrite(str(lab_path), labels)
        cv2.imwrite(str(conf_path), np.round(conf * 255).astype(np.uint8))


def _read_labels(path: Path) -> np.ndarray:
    labels = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if labels is None:
        raise OSError(f"cannot read label map {path}")
    return labels


def _read_conf(path: Path) -> np.ndarray:
    return _read_labels(path).astype(np.float32) / 255.0


def label_frame(frame: Frame, segmenter: Segmenter, label_size: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    """Segment one frame upright and return labels in sensor orientation at ``label_size``."""
    k = upright_quarter_turns(frame.T_world_cam)
    upright = to_upright(frame.rgb(), k)
    w, h = label_size
    out_size = (h, w) if k % 2 else (w, h)
    labels, conf = segmenter.predict(upright, out_size)
    return from_upright(labels, k), from_upright(conf, k)


def run_semantics(
    frameset: FrameSet,
    config: SemanticsConfig | None = None,
    segmenter: Segmenter | None = None,
    cache_root: Path | None = None,
) -> SemanticsResult:
    cfg = config or SemanticsConfig()
    root = cache_root or frameset.cache_dir or Path("outputs") / "cache" / frameset.meta.capture_id
    model_name = segmenter.name if segmenter is not None else f"{cfg.model_key}@{cfg.short_side}"
    cache = _LabelCache(Path(root), model_name, cfg.label_size)

    chosen = select_frames(frameset.frames, cfg.max_frames)
    from_cache = sum(cache.has(frameset.frames[i].index) for i in chosen)
    if from_cache < len(chosen) and segmenter is None:
        from scan.semantics.segmenter import SegFormerSegmenter

        segmenter = SegFormerSegmenter(cfg.model_key, short_side=cfg.short_side)

    frames = list(frameset.frames)
    for i in chosen:
        frame = frames[i]
        if not cache.has(frame.index):
            labels, conf = label_frame(frame, segmenter, cfg.label_size)
            cache.save(frame.index, labels, conf)
        lab_path, conf_path = cache.paths(frame.index)
        frames[i] = replace(
            frame,
            label_size=cfg.label_size,
            _labels=lambda p=lab_path: _read_labels(p),
            _label_conf=lambda p=conf_path: _read_conf(p),
        )

    labelled = [frames[i] for i in chosen]
    summary = _summarise(frameset, model_name, labelled, from_cache)
    return SemanticsResult(frameset=frameset.with_frames(frames), summary=summary)


def _summarise(frameset: FrameSet, model_name: str, labelled: list[Frame], from_cache: int) -> SemanticSummary:
    counts = np.zeros(len(Surface), np.int64)
    mirror_frames = 0
    for frame in labelled:
        hist = np.bincount(frame.labels().ravel(), minlength=len(Surface))
        counts += hist
        if hist[Surface.MIRROR] / hist.sum() > MIRROR_FRAME_SHARE:
            mirror_frames += 1
    total = counts.sum() or 1
    shares = {s.name.lower(): round(float(counts[s] / total), 3) for s in Surface}
    agreement = geometry_agreement(labelled)

    issues: list[Issue] = []
    if not labelled:
        issues.append(Issue("NO_LABELLED_FRAMES", WARNING, "no sharp frames were available to label",
                            "Move more slowly so frames are not blurred."))
    if mirror_frames:
        issues.append(Issue(
            "MIRROR_SEEN", INFO,
            f"mirrors detected in {mirror_frames} frames; depth seen through them is excluded from geometry",
            None,
        ))
    for name, stats in agreement.items():
        if stats["agreement"] is not None and stats["pixels"] > 2000 and stats["agreement"] < LOW_AGREEMENT:
            issues.append(Issue(
                "LOW_LABEL_AGREEMENT", WARNING,
                f"only {stats['agreement']:.0%} of '{name}' labels match the geometry; labels are less reliable",
                "Improve lighting and avoid extreme close-ups.",
            ))
    return SemanticSummary(
        capture_id=frameset.meta.capture_id,
        model=model_name,
        n_frames_labelled=len(labelled),
        n_frames_from_cache=from_cache,
        class_shares=shares,
        mirror_frames=mirror_frames,
        agreement=agreement,
        issues=issues,
    )


def write_overlays(frameset: FrameSet, out_dir: Path, count: int = 6) -> list[Path]:
    """Upright RGB with labels blended on top, for checking by eye."""
    labelled = [f for f in frameset.frames if f.has_labels]
    if not labelled:
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    picks = np.unique(np.linspace(0, len(labelled) - 1, min(count, len(labelled))).round().astype(int))
    paths = []
    for j in picks:
        frame = labelled[j]
        labels = frame.labels()
        rgb = cv2.resize(frame.rgb(), (labels.shape[1], labels.shape[0]), interpolation=cv2.INTER_AREA)
        blend = (0.55 * rgb + 0.45 * colourise(labels)).astype(np.uint8)
        k = upright_quarter_turns(frame.T_world_cam)
        image = cv2.cvtColor(to_upright(blend, k), cv2.COLOR_RGB2BGR)
        _draw_legend(image)
        path = out_dir / f"overlay_{frame.index:06d}.jpg"
        cv2.imwrite(str(path), image)
        paths.append(path)
    return paths


def _draw_legend(image: np.ndarray) -> None:
    from scan.semantics.classes import COLOURS_RGB

    y = 16
    for surface, rgb in COLOURS_RGB.items():
        cv2.rectangle(image, (6, y - 10), (18, y + 2), rgb[::-1], -1)
        cv2.putText(image, surface.name.lower(), (24, y), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
        y += 16
