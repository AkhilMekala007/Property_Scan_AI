"""C12 calibration: scale each tier's interval widths so ~90 % of ground truth falls inside.

Input: benchmark reports (``scan bench`` JSON). For every gated row the normalised error is
z = |ours - truth| / sigma (sigma recovered from the reported 90 % interval). Per (tier, kind) the
factor is k = q90(z) / Z90: multiplying every sigma by k makes 90 % of these rows fall inside.

Values never change - only interval widths, and only wider (k >= 1). With few rows the factor is
uncertain, so the output
records n and the leave-one-out coverage (each row checked with a factor fitted without it), and
kinds with fewer than ``MIN_ROWS`` rows keep their provisional budget (calibrated: false).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np

from scan.contract.budget import Z90

MIN_ROWS = 4
KIND_OF_FIELD = {"length": "wall", "ceiling_height": "ceiling", "width": "opening"}


@dataclass
class Factor:
    tier: str
    kind: str
    k: float
    n: int
    coverage_before: float
    coverage_after: float
    coverage_loo: float  # leave-one-out: honest estimate for new captures


def _z(row: dict) -> float | None:
    if row["ours"] is None or row["hi"] is None or row["passed"] is None:
        return None
    sigma = (row["hi"] - row["lo"]) / (2 * Z90)
    return abs(row["ours"] - row["truth"]) / sigma if sigma > 0 else None


def _k(z: np.ndarray) -> float:
    # 90th percentile with the conservative (higher) order statistic for small n; never below 1:
    # a handful of rows is not enough evidence to make intervals narrower than the error budget
    # (+1 %: the defining row would otherwise sit exactly on the rounded interval edge)
    return max(float(np.quantile(z, 0.9, method="higher")) / Z90 * 1.01, 1.0)


def fit(report_paths: list[Path]) -> list[Factor]:
    rows: dict[tuple[str, str], list[float]] = {}
    for p in report_paths:
        rep = json.loads(Path(p).read_text(encoding="utf-8"))
        for row in rep["rows"]:
            z = _z(row)
            if z is not None:
                rows.setdefault((rep["tier"], row["kind"]), []).append(z)
    out = []
    for (tier, kind), zs in sorted(rows.items()):
        z = np.array(zs)
        if len(z) < MIN_ROWS:
            continue
        k = _k(z)
        loo = np.mean([z[i] / _k(np.delete(z, i)) <= Z90 for i in range(len(z))])
        out.append(Factor(tier, kind, round(k, 3), len(z), round(float(np.mean(z <= Z90)), 3),
                          round(float(np.mean(z / k <= Z90)), 3), round(float(loo), 3)))
    return out


def write_factors(factors: list[Factor], path: Path, sources: list[Path]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "method": "per (tier, kind): sigma *= q90(|error| / sigma) / 1.6449, fitted on the tape benchmark",
        "min_rows": MIN_ROWS,
        "sources": [str(s).replace("\\", "/") for s in sources],
        "factors": [asdict(f) for f in factors],
    }, indent=2) + "\n", encoding="utf-8")
    return path


def load_factors(path: Path) -> dict[tuple[str, str], float]:
    if not Path(path).exists():
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {(f["tier"], f["kind"]): f["k"] for f in data["factors"]}


def _rescale(m: dict, k: float) -> None:
    sigma = m["sigma"] * k
    m["sigma"] = round(sigma, 5)
    if m["bound"] == "two_sided":
        m["lo"] = round(max(0.0, m["value"] - Z90 * sigma), 4)
        m["hi"] = round(m["value"] + Z90 * sigma, 4)
    m["calibrated"] = True


def apply(result: dict, factors: dict[tuple[str, str], float]) -> dict:
    """Recompute intervals of a result.json with calibrated factors (values untouched)."""
    tier = result["capture"]["tier"]
    for r in result["rooms"]:
        for w in r["walls"]:
            if (tier, "wall") in factors:
                _rescale(w["length"], factors[(tier, "wall")])
        if r.get("ceiling_height") and (tier, "ceiling") in factors:
            _rescale(r["ceiling_height"], factors[(tier, "ceiling")])
    for o in result.get("openings", []):
        if (tier, "opening") in factors:
            _rescale(o["width"], factors[(tier, "opening")])
    calibrated = sorted(kind for t, kind in factors if t == tier)
    result["processing"]["calibrated"] = bool(calibrated)
    if calibrated:
        result["processing"]["interval_method"] = (
            f"error budget with benchmark-fitted factors for {', '.join(calibrated)} (C12); other quantities "
            "keep the provisional budget (calibrated: false)")
    return result
