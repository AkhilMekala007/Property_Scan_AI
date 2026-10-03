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

import numpy as np
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
AREA_GATE = "info (no area gate in the brief)"
MIN_WALL_M = 1.0  # walls shorter than this are jogs / reveals, not a room dimension


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
        if "floor_area" in r:
            out[f"{r['name']}.floor_area"] = r["floor_area"]
    for o in result["openings"]:
        out[f"opening_{o['id']}"] = o["width"]
    return out


def _truth_items(gt: dict):
    """(label, kind, value) for every measured quantity in the ground-truth file."""
    for room, data in gt.get("rooms", {}).items():
        if not isinstance(data, dict) or "of" in data or any(k in data for k in ("dims", "depth", "area")):
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
    if item in ("ceiling", "area"):
        return data.get(item)
    if item.startswith("dim"):  # overall dimensions, longest first (same rule as the truth)
        dims = sorted(data.get("dims", []), reverse=True)
        j = int(item[3:].split(".")[0])
        return dims[j] if j < len(dims) else None
    if item in (data.get("walls") or {}):
        return data["walls"][item]
    return (data.get("openings") or {}).get(item)


def _axes(polygon_xz: list) -> list[tuple[list[int], list[float]]]:
    """Room walls grouped by direction (two Manhattan axes): [(wall indices, lengths), ...]."""
    P = np.array(polygon_xz, float)
    groups: dict[int, tuple[list[int], list[float]]] = {}
    ref = None
    for k in range(len(P)):
        d = P[(k + 1) % len(P)] - P[k]
        L = float(np.hypot(*d))
        if L < 1e-6:
            continue
        ang = np.degrees(np.arctan2(d[1], d[0])) % 180
        ref = ang if ref is None else ref
        axis = 0 if min(abs(ang - ref), 180 - abs(ang - ref)) < 45 else 1
        groups.setdefault(axis, ([], []))
        groups[axis][0].append(k)
        groups[axis][1].append(L)
    return [groups.get(0, ([], [])), groups.get(1, ([], []))]


def _dimension_rows(result: dict, gt: dict, mapping: dict):
    """(label, kind, truth, ours_id) for rooms given as overall dimensions / depth / area.

    Rule: a room dimension (laser, wall to wall) is compared with the longest wall on that axis;
    the longer dimension goes with the axis whose longest wall is longer; ``depth`` is the longer
    axis. (Revision 1, after the first run: the original rule compared every wall >= 1 m and paired
    axes by mean wall length, which swapped axes on irregular rooms and gated partial walls beside
    jogs against the full room dimension.)
    """
    by_name = {r["name"]: r for r in result["rooms"] if "name" in r}
    for room, data in gt.get("rooms", {}).items():
        if not isinstance(data, dict) or not any(k in data for k in ("dims", "depth", "area")):
            continue
        ours = by_name.get(mapping.get(room, ""))
        if data.get("area"):
            yield f"{room}.area", "area", float(data["area"]), (f"{ours['name']}.floor_area" if ours else None)
        if data.get("ceiling"):
            yield f"{room}.ceiling", "ceiling", float(data["ceiling"]), (ours["ceiling_id"] if ours else None)
        dims = sorted([float(x) for x in data.get("dims", [])], reverse=True)
        if data.get("depth"):
            dims = [float(data["depth"])]
        if not dims:
            continue
        if ours is None:
            for j, d in enumerate(dims):
                yield f"{room}.dim{j}", "wall", d, None
            continue
        axes = []  # per axis: its longest wall (a room dimension is wall-to-wall across the room)
        for idx, lens in _axes(ours["polygon_xz"]):
            if lens:
                j = int(np.argmax(lens))
                axes.append((lens[j], idx[j]))
        axes.sort(key=lambda a: -a[0])
        for j, d in enumerate(dims):
            if j >= len(axes) or axes[j][0] < MIN_WALL_M:
                yield f"{room}.dim{j}", "wall", d, None
                continue
            yield f"{room}.dim{j}", "wall", d, f"{ours['name']}.wall_{axes[j][1]}"


def benchmark(result_path: Path, gt_path: Path, capture: str) -> BenchReport:
    result = json.loads(Path(result_path).read_text(encoding="utf-8"))
    gt = yaml.safe_load(Path(gt_path).read_text(encoding="utf-8"))
    tier = result["capture"]["tier"]
    ours = _index(result)
    mapping = (gt.get("mapping") or {}).get(capture, {})
    rows = []
    items = [(label, kind, truth, mapping.get(label)) for label, kind, truth in _truth_items(gt)]
    items += list(_dimension_rows(result, gt, mapping))
    approx = {f"{room}.{field}" for room, data in gt.get("rooms", {}).items() if isinstance(data, dict)
              for field in data.get("approx", [])}
    for label, kind, truth, ours_id in items:
        if kind == "area":
            gate_text, gate = AREA_GATE, (lambda e, t: None)
        elif label in approx:
            gate_text, gate = "info (approximate truth)", (lambda e, t: None)
        else:
            gate_text, gate = GATES[(tier, kind)]
        m = ours.get(ours_id) if ours_id else None
        if m is None:
            rows.append(Row(label, kind, ours_id, truth, None, None, None, None, None, gate_text,
                            None if kind == "area" else False))
            continue
        err = m["value"] - truth
        inside = m["lo"] <= truth <= (m["hi"] if m["hi"] is not None else float("inf"))
        g = gate(err, truth)
        row = Row(label, kind, ours_id, truth, m["value"], m["lo"], m["hi"], round(err, 4), inside, gate_text,
                  None if g is None else bool(g))
        app = None if label in approx else _app_value(gt, label)
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
        sel = [r for r in sel if r.passed is not None]
        return {"passed": sum(r.passed for r in sel), "total": len(sel),
                "rate": round(sum(r.passed for r in sel) / len(sel), 3) if sel else None}

    measured = [r for r in rows if r.ours is not None]
    h2h = [r for r in rows if r.verdict]
    out = {kind: rate(r for r in rows if r.kind == kind) for kind in ("wall", "ceiling", "opening")}
    out["missed"] = sum(1 for r in rows if r.ours is None)  # unmapped / not detected: counts as a miss
    gated = [r for r in measured if r.passed is not None]
    out["interval_coverage"] = round(sum(r.inside for r in gated) / len(gated), 3) if gated else None
    scored = [r for r in measured if r.passed is not None or (r.kind == "area" and "approx" not in r.gate)]
    out["median_abs_error_m"] = {  # gated rows only (approximate truths excluded); area in m2
        kind: round(float(np.median([abs(r.error) for r in scored if r.kind == kind])), 4)
        for kind in ("wall", "ceiling", "opening", "area") if any(r.kind == kind for r in scored)}
    out["median_abs_error_pct"] = {
        kind: round(float(np.median([abs(r.error) / r.truth * 100 for r in scored if r.kind == kind])), 2)
        for kind in ("wall", "ceiling", "opening", "area") if any(r.kind == kind for r in scored)}
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
        err = "missed" if r.error is None else (f"{r.error:+.2f} m2" if r.kind == "area" else f"{r.error * 100:+.1f} cm")
        app = "" if r.app is None else f"{r.app:.3f}"
        app_err = "" if r.app_error is None else (f"{r.app_error:+.2f} m2" if r.kind == "area" else f"{r.app_error * 100:+.1f} cm")
        lines.append(f"| {r.key} | {r.ours_id or '—'} | {r.truth:.3f} | {ours} | {err} | "
                     f"{'' if r.inside is None else ('yes' if r.inside else 'no')} | {r.gate} | "
                     f"{'—' if r.passed is None else ('✓' if r.passed else '✗')} | {app} | {app_err} | {r.verdict or ''} |")
    lines += ["", "## Summary", "", "```", json.dumps(rep.summary, indent=2), "```"]
    return "\n".join(lines) + "\n"


def to_json(rep: BenchReport) -> str:
    return json.dumps({"capture": rep.capture, "tier": rep.tier, "summary": rep.summary,
                       "rows": [asdict(r) for r in rep.rows]}, indent=2)
