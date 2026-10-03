"""Segmenter interface and the SegFormer implementation."""

from __future__ import annotations

from typing import Protocol

import cv2
import numpy as np

from scan.semantics.classes import Surface, build_lookup

IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], np.float32)


class Segmenter(Protocol):
    name: str  # identifies the model in cache manifests

    def predict(self, rgb: np.ndarray, out_size: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
        """Upright RGB image -> (Surface labels, confidence 0-1), both of size ``out_size`` (w, h)."""
        ...


class SegFormerSegmenter:
    """ADE20K SegFormer on CPU, loaded from local weights only."""

    def __init__(self, model_key: str = "segformer-b2-ade", short_side: int = 512, threads: int = 4):
        import torch
        from transformers import SegformerForSemanticSegmentation

        from scan.models import require_weights

        torch.set_num_threads(threads)
        path = require_weights(model_key)
        self._torch = torch
        self.model = SegformerForSemanticSegmentation.from_pretrained(path, local_files_only=True).eval()
        id2label = {int(k): v for k, v in self.model.config.id2label.items()}
        self.lookup = build_lookup(id2label)
        self._lookup_t = torch.from_numpy(self.lookup.astype(np.int64))
        self.n_surfaces = len(Surface)
        self.short_side = short_side
        self.name = f"{model_key}@{short_side}"

    def predict(self, rgb: np.ndarray, out_size: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
        torch = self._torch
        h, w = rgb.shape[:2]
        scale = self.short_side / min(h, w)
        resized = cv2.resize(rgb, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
        x = (resized.astype(np.float32) / 255.0 - IMAGENET_MEAN) / IMAGENET_STD
        x = torch.from_numpy(x.transpose(2, 0, 1)).unsqueeze(0)
        with torch.inference_mode():
            logits = self.model(pixel_values=x).logits[0]  # (150, h/4, w/4)
            probs = logits.softmax(dim=0)
            # Sum ADE probabilities per Surface (wall + painting + poster all vote "wall"),
            # at the model's low resolution, then upsample only the few Surface channels.
            surface = torch.zeros((self.n_surfaces, *probs.shape[1:]), dtype=probs.dtype)
            surface.index_add_(0, self._lookup_t, probs)
            surface = torch.nn.functional.interpolate(
                surface[None], size=(out_size[1], out_size[0]), mode="bilinear", align_corners=False
            )[0]
            conf, labels = surface.max(dim=0)
        return labels.numpy().astype(np.uint8), conf.numpy().astype(np.float32)
