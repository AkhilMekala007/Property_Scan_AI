"""Download pretrained model weights into weights/. The only step that uses the network.

Usage:
    python scripts/fetch_weights.py            # every model in the registry
    python scripts/fetch_weights.py segformer-b2-ade
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from huggingface_hub import snapshot_download  # noqa: E402

from scan.models import MODELS  # noqa: E402

ALLOW = ["*.json", "*.safetensors", "*.txt"]


def fetch(key: str) -> Path:
    spec = MODELS[key]
    print(f"{key}: {spec.repo_id}@{spec.revision}  ({spec.licence})")
    path = snapshot_download(
        repo_id=spec.repo_id,
        revision=spec.revision,
        local_dir=spec.local_dir,
        allow_patterns=ALLOW,
    )
    if not any(Path(path).glob("*.safetensors")):
        # Older repos only ship pytorch_model.bin.
        path = snapshot_download(
            repo_id=spec.repo_id,
            revision=spec.revision,
            local_dir=spec.local_dir,
            allow_patterns=ALLOW + ["*.bin"],
        )
    size_mb = sum(p.stat().st_size for p in Path(path).rglob("*") if p.is_file()) / 1e6
    print(f"  -> {spec.local_dir}  ({size_mb:.0f} MB)")
    return Path(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("models", nargs="*", help=f"model keys (default: all): {', '.join(MODELS)}")
    args = parser.parse_args()
    keys = args.models or list(MODELS)
    unknown = [k for k in keys if k not in MODELS]
    if unknown:
        parser.error(f"unknown model(s): {', '.join(unknown)}")
    for key in keys:
        fetch(key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
