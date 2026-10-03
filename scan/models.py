"""Registry of every pretrained model the pipeline uses.

Weights are downloaded once by ``scripts/fetch_weights.py`` into ``weights/``;
at run time models load from there only, so the pipeline never touches the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

WEIGHTS_DIR = Path(__file__).resolve().parents[1] / "weights"


@dataclass(frozen=True)
class ModelSpec:
    key: str
    repo_id: str  # Hugging Face repository
    revision: str  # pinned commit/branch for reproducibility
    licence: str
    purpose: str

    @property
    def local_dir(self) -> Path:
        return WEIGHTS_DIR / self.key


MODELS: dict[str, ModelSpec] = {
    spec.key: spec
    for spec in (
        ModelSpec(
            key="segformer-b0-ade",
            repo_id="nvidia/segformer-b0-finetuned-ade-512-512",
            revision="main",
            licence="NVIDIA Source Code License (non-commercial)",
            purpose="C4 surface labels, fast variant",
        ),
        ModelSpec(
            key="segformer-b2-ade",
            repo_id="nvidia/segformer-b2-finetuned-ade-512-512",
            revision="main",
            licence="NVIDIA Source Code License (non-commercial)",
            purpose="C4 surface labels, accurate variant",
        ),
        ModelSpec(
            key="owlv2-base",
            repo_id="google/owlv2-base-patch16-ensemble",
            revision="main",
            licence="Apache-2.0",
            purpose="C10 open-vocabulary damage detection (text prompts)",
        ),
        ModelSpec(
            key="owlvit-b32",
            repo_id="google/owlvit-base-patch32",
            revision="main",
            licence="Apache-2.0",
            purpose="C10 open-vocabulary damage detection, CPU-fast variant (default)",
        ),
        ModelSpec(
            key="sam2-tiny",
            repo_id="facebook/sam2.1-hiera-tiny",
            revision="main",
            licence="Apache-2.0",
            purpose="C10 damage masks from detection boxes",
        ),
    )
}


class WeightsMissingError(FileNotFoundError):
    pass


def require_weights(key: str) -> Path:
    spec = MODELS[key]
    if not (spec.local_dir / "config.json").exists():
        raise WeightsMissingError(
            f"weights for '{key}' not found in {spec.local_dir}; "
            f"run: python scripts/fetch_weights.py {key}"
        )
    return spec.local_dir
