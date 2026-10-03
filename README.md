# Property Scan AI

Handheld iPhone capture → dimensioned, stitched whole-property floor plan with
per-surface damage regions, concealed-damage flags, scope line items, and a
calibrated confidence interval on every measurement.

Three input tiers, one output contract:

| Tier | Input | Hardware |
|------|-------|----------|
| Photos | 2–8 stills per room, one folder per room | Any iPhone 15+ |
| Video | Handheld walkthrough clip | Any iPhone 15+ |
| LiDAR | Depth + poses + intrinsics | Pro-class iPhones |

> 🚧 Work in progress. Design: [docs/HLD.md](docs/HLD.md) ·
> [visual overview](docs/HLD_visual.html) · LLDs in [docs/LLD](docs/LLD).

## Setup

Requires Python 3.11+ (tested on 3.14, Windows, CPU only).

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows; use .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
pip install -e .
```

## Usage (so far)

Inspect a capture: detects the tier, validates the files, selects keyframes and
prints a summary. `--topdown` also saves a top-down sanity render to `outputs/`.

```bash
scan inspect path/to/capture --topdown
```

## Tests

```bash
pytest              # fast unit tests on synthetic captures
pytest -m sample    # also load the provided sample captures (slow, needs the data locally)
```
