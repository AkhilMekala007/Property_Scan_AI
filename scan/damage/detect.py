"""Open-vocabulary damage detection on keyframes, with decoy prompts and a disk cache."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np

from scan.core.types import Frame
from scan.semantics.upright import to_upright, upright_quarter_turns

# Each class is detected with a few phrasings; its score is the best over them.
DAMAGE_PROMPTS: dict[str, list[str]] = {
    "water_stain": ["a water stain on a wall", "a water stain on a ceiling", "a brown water damage mark"],
    "crack": ["a crack in a wall", "a crack in plaster", "a crack in a ceiling"],
    "mold": ["black mold on a wall", "mold spots on a ceiling"],
}
# Decoys soak up things that look a bit like damage; a damage box mostly inside a stronger
# decoy box is dropped.
DECOY_PROMPTS = ["a poster", "a painting", "a picture frame", "a light switch", "an electrical outlet",
                 "a shadow", "a door", "a window", "a lamp", "a curtain", "a cable", "a shelf"]
PROMPT_VERSION = 1


@dataclass(frozen=True)
class DetectConfig:
    model_key: str = "owlvit-b32"
    max_frames: int = 40
    score_threshold: float = 0.18  # clean sample walls never exceeded 0.14
    decoy_overlap: float = 0.6  # share of a damage box covered by a stronger decoy box
    max_box_frac: float = 0.25  # bigger boxes are scene structure (corner lines, cabinets), not damage


@dataclass
class Detection:
    frame_index: int
    cls: str
    score: float
    box: tuple[float, float, float, float]  # x0, y0, x1, y1 in sensor-orientation RGB pixels
    prompt: str = ""


class Detector(Protocol):
    name: str

    def detect(self, upright_rgb: np.ndarray) -> list[tuple[str, str, float, tuple]]:
        """-> [(class or 'decoy', prompt, score, (x0, y0, x1, y1) in upright pixels)]"""
        ...


class OwlDetector:
    def __init__(self, model_key: str = "owlvit-b32", threads: int = 4):
        import torch
        from transformers import AutoProcessor, Owlv2ForObjectDetection, OwlViTForObjectDetection

        from scan.models import require_weights

        torch.set_num_threads(threads)
        path = require_weights(model_key)
        cls = Owlv2ForObjectDetection if "owlv2" in model_key else OwlViTForObjectDetection
        self._torch = torch
        self.processor = AutoProcessor.from_pretrained(path, local_files_only=True)
        self.model = cls.from_pretrained(path, local_files_only=True).eval()
        self.prompts = [(c, p) for c, ps in DAMAGE_PROMPTS.items() for p in ps] + [("decoy", p) for p in DECOY_PROMPTS]
        self.name = f"{model_key}@v{PROMPT_VERSION}"

    def detect(self, upright_rgb):
        from PIL import Image

        torch = self._torch
        image = Image.fromarray(upright_rgb)
        inputs = self.processor(text=[[p for _, p in self.prompts]], images=image, return_tensors="pt")
        with torch.inference_mode():
            out = self.model(**inputs)
        size = torch.tensor([image.size[::-1]])
        res = self.processor.post_process_grounded_object_detection(out, threshold=0.05, target_sizes=size)[0]
        return [(self.prompts[int(l)][0], self.prompts[int(l)][1], float(s), tuple(float(v) for v in b))
                for s, l, b in zip(res["scores"], res["labels"], res["boxes"])]


def _box_to_sensor(box, k: int, w_up: int, h_up: int):
    """Map an upright-image box back to the sensor orientation (inverse of np.rot90(k))."""
    x0, y0, x1, y1 = box
    pts = np.array([[x0, y0], [x1, y0], [x0, y1], [x1, y1]], float)
    w, h = w_up, h_up
    for _ in range(k % 4):  # undo one CCW quarter turn at a time: (x, y) -> (h - 1 - y, x)
        pts = np.column_stack([h - 1 - pts[:, 1], pts[:, 0]])
        w, h = h, w
    return float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max())


def _covered_by(a, b) -> float:
    """Share of box a inside box b."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area = max((a[2] - a[0]) * (a[3] - a[1]), 1e-9)
    return ix * iy / area


def filter_detections(raw, cfg: DetectConfig, image_area: float | None = None):
    """Keep damage boxes above threshold that no stronger decoy box explains; best class per box.

    Boxes covering more than ``max_box_frac`` of the image are dropped: on the sample captures
    every such "crack" was a wall/ceiling corner line or a cabinet edge.
    """
    decoys = [r for r in raw if r[0] == "decoy"]
    out = []
    for cls, prompt, score, box in raw:
        if cls == "decoy" or score < cfg.score_threshold:
            continue
        if image_area and (box[2] - box[0]) * (box[3] - box[1]) > cfg.max_box_frac * image_area:
            continue
        if any(d[2] > score and _covered_by(box, d[3]) >= cfg.decoy_overlap for d in decoys):
            continue
        out.append((cls, prompt, score, box))
    out.sort(key=lambda r: -r[2])
    kept = []  # one class per location: drop weaker boxes overlapping a kept one
    for r in out:
        if all(_covered_by(r[3], k[3]) < 0.5 for k in kept):
            kept.append(r)
    return kept


def select_frames(frames: list[Frame], max_frames: int) -> list[Frame]:
    usable = [f for f in frames if f.rgb_ok and f.has_labels and f.has_depth and f.T_world_cam is not None]
    if len(usable) <= max_frames:
        return usable
    picks = np.unique(np.linspace(0, len(usable) - 1, max_frames).round().astype(int))
    return [usable[i] for i in picks]


def run_detection(frames: list[Frame], cache_dir: Path, cfg: DetectConfig,
                  detector: Detector | None = None) -> tuple[list[Detection], int]:
    """Detections for the selected frames (cached per frame); returns (detections, frames from cache)."""
    chosen = select_frames(frames, cfg.max_frames)
    name = detector.name if detector is not None else f"{cfg.model_key}@v{PROMPT_VERSION}"
    cdir = cache_dir / "damage" / name.replace("@", "_")
    cdir.mkdir(parents=True, exist_ok=True)
    detections, cached = [], 0
    for f in chosen:
        path = cdir / f"{f.index:06d}.json"
        if path.exists():
            raw = [tuple(r) for r in json.loads(path.read_text(encoding="utf-8"))]
            cached += 1
        else:
            if detector is None:
                detector = OwlDetector(cfg.model_key)
            k = upright_quarter_turns(f.T_world_cam)
            up = to_upright(f.rgb(), k)
            raw = [(c, p, s, _box_to_sensor(b, k, up.shape[1], up.shape[0])) for c, p, s, b in detector.detect(up)]
            path.write_text(json.dumps(raw), encoding="utf-8")
        image_area = float(f.intrinsics.width * f.intrinsics.height)
        for cls, prompt, score, box in filter_detections(raw, cfg, image_area):
            detections.append(Detection(f.index, cls, round(score, 4), tuple(box), prompt))
    return detections, cached


def to_dicts(dets: list[Detection]) -> list[dict]:
    return [asdict(d) for d in dets]
