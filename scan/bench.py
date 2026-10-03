"""C14 benchmark: our result.json vs laser ground truth (and a consumer app) -> gate tables.

Ground truth and the mapping from its labels to our surface ids live in one YAML file
(template: bench/ground_truth/TEMPLATE.yaml). Every dimension yields: our value and interval,
the true value, our error, whether the truth is inside our 90 % interval, the gate result,
and - when the app's number is given - the app's error and who wins.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

TIE_M = 0.005  # errors within 5 mm of each other count as a tie (the brief does not define "tie")

# Gates from the brief. Wall-length accuracy at the LiDAR tier is not given (Round-1 gates were not
# shared); 1 cm / 0.5 % (the repeatability tolerance) is used as the stand-in and labelled as such.
GATES = {
    ("lidar", "wall"): ("<= 1 cm or 0.5 % (stand-in)", lambda e, t: abs(e) <= max(0.01, 0.005 * t)),
    ("lidar", "ceiling"): ("<= 1.5 cm", lambda e, t: abs(e) <= 0.015),
    ("lidar", "opening"): ("<= 2 cm", lambda e, t: abs(e) <= 0.02),
    ("video", "wall"): ("+-3 %", lambda e, t: abs(e) <= 0.03 * t),
    ("video", "ceiling"): ("+-3 % (stand-in)", lambda e, t: abs(e) <= 0.03 * t),
    ("video", "opening"): ("<= 2 cm", lambda e, t: abs(e) <= 0.02),
    ("photo", "wall"): ("+-8 %", lambda e, t: abs(e) <= 0.08 * t),
    ("photo", "ceiling"): ("+-8 % (stand-in)", lambda e, t: abs(e) <= 0.08 * t),
    ("photo", "opening"): ("<= 2 cm", lambda e, t: abs(e) <= 0.02),
}


@dataclass
class Row:
    key: str  # ground-truth label, e.g. bedroom1.A
    kind: str  # wall | ceiling | opening
    ours_id: str | None
    truth: float
    ours: float | None
    lo: float | None
    hi: float | None
    error: float | None
    inside: bool | None
    gate: str
    passed: bool | None
    app: float | None = None
    app_error: float | None = None
    verdict: str | None = None  # win | tie | loss


@dataclass
class BenchReport:
    capture: str
    tier: str
    rows: list[Row]
    summary: dict = field(default_factory=dict)


def _index(result: dict) -> dict[str, dict]:
    """Every measurable item of a result.json by id: walls, ceilings, openings."""
    out = {}
    for r in result["rooms"]:
        for w in r["walls"]:
            out[w["id"]] = w["length"]
        if r["ceiling_height"]:
            out[r["ceiling_id"]] = r["ceiling_height"]
    for o in result["openings"]:
        out[f"opening_{o['id']}"] = o["width"]
    return out


def _truth_items(gt: dict):
    """(label, kind, value) for every measured quantity in the ground-truth file."""
    for room, data in gt.get("rooms", {}).items():
        if not isinstance(data, dict) or "of" in data:
            continue
        for wall, v in (data.get("walls") or {}).items():
            yield f"{room}.{wall}", "wall", float(v)
        if data.get("ceiling"):
            yield f"{room}.ceiling", "ceiling", float(data["ceiling"])
        for kind in ("doors", "windows"):
            for k, op in enumerate(data.get(kind) or []):
                yield f"{room}.{kind[:-1]}_{op.get('id', op.get('wall', k))}", "opening", float(op["width"])


def _app_value(gt: dict, label: str) -> float | None:
    comp = (gt.get("competitor") or {}).get("rooms", {})
    room, _, item = label.partition(".")
    data = comp.get(room)
    if not data:
        return None
    if item == "ceiling":
        return data.get("ceiling")
    if item in (data.get("walls") or {}):
        return data["walls"][item]
    return (data.get("openings") or {}).get(item)


def benchmark(result_path: Path, gt_path: Path, capture: str) -> BenchReport:
    result = json.loads(Path(result_path).read_text(encoding="utf-8"))
    gt = yaml.safe_load(Path(gt_path).read_text(encoding="utf-8"))
    tier = result["capture"]["tier"]
    ours = _index(result)
    mapping = (gt.get("mapping") or {}).get(capture, {})
    rows = []
    for label, kind, truth in _truth_items(gt):
        gate_text, gate = GATES[(tier, kind)]
        ours_id = mapping.get(label)
        m = ours.get(ours_id) if ours_id else None
        if m is None:
            rows.append(Row(label, kind, ours_id, truth, None, None, None, None, None, gate_text, False))
            continue
        err = m["value"] - truth
        inside = m["lo"] <= truth <= (m["hi"] if m["hi"] is not None else float("inf"))
        row = Row(label, kind, ours_id, truth, m["value"], m["lo"], m["hi"], round(err, 4), inside, gate_text,
                  bool(gate(err, truth)))
        app = _app_value(gt, label)
        if app is not None:
            row.app = float(app)
            row.app_error = round(float(app) - truth, 4)
            diff = abs(err) - abs(row.app_error)
            row.verdict = "tie" if abs(diff) <= TIE_M else ("win" if diff < 0 else "loss")
        rows.append(row)
    return BenchReport(capture, tier, rows, _summary(rows))


def _summary(rows: list[Row]) -> dict:
    def rate(sel):
        sel = list(sel)
        return {"passed": sum(r.passed for r in sel), "total": len(sel),
                "rate": round(sum(r.passed for r in sel) / len(sel), 3) if sel else None}

    measured = [r for r in rows if r.ours is not None]
    h2h = [r for r in rows if r.verdict]
    out = {kind: rate(r for r in rows if r.kind == kind) for kind in ("wall", "ceiling", "opening")}
    out["missed"] = sum(1 for r in rows if r.ours is None)  # unmapped / not detected: counts as a miss
    out["interval_coverage"] = round(sum(r.inside for r in measured) / len(measured), 3) if measured else None
    out["median_abs_error_m"] = {
        kind: round(sorted(abs(r.error) for r in measured if r.kind == kind)[len([r for r in measured if r.kind == kind]) // 2], 4)
        for kind in ("wall", "ceiling", "opening") if any(r.kind == kind for r in measured)}
    if h2h:
        good = sum(r.verdict in ("win", "tie") for r in h2h)
        out["head_to_head"] = {"beat_or_tie": good, "total": len(h2h), "rate": round(good / len(h2h), 3),
                               "pass": good / len(h2h) >= 0.70}
    return out


def to_markdown(rep: BenchReport) -> str:
    lines = [f"# Benchmark: {rep.capture} ({rep.tier} tier)", "",
             "| Item | Ours id | Truth | Ours [90 % interval] | Error | Inside | Gate | Pass | App | App error | Verdict |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rep.rows:
        ours = "—" if r.ours is None else f"{r.ours:.3f} [{r.lo:.3f}, {'—' if r.hi is None else f'{r.hi:.3f}'}]"
        err = "missed" if r.error is None else f"{r.error * 100:+.1f} cm"
        app = "" if r.app is None else f"{r.app:.3f}"
        app_err = "" if r.app_error is None else f"{r.app_error * 100:+.1f} cm"
        lines.append(f"| {r.key} | {r.ours_id or '—'} | {r.truth:.3f} | {ours} | {err} | "
                     f"{'' if r.inside is None else ('yes' if r.inside else 'no')} | {r.gate} | "
                     f"{'✓' if r.passed else '✗'} | {app} | {app_err} | {r.verdict or ''} |")
    lines += ["", "## Summary", "", "```", json.dumps(rep.summary, indent=2), "```"]
    return "\n".join(lines) + "\n"


def to_json(rep: BenchReport) -> str:
    return json.dumps({"capture": rep.capture, "tier": rep.tier, "summary": rep.summary,
                       "rows": [asdict(r) for r in rep.rows]}, indent=2)
