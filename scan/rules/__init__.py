"""C11 Rules: concealed-damage flags and scope line items from damage regions.

Rules live in a YAML file (rules/damage_rules.yaml): conditions on class, surface kind,
size and height; actions are a flag (with the rule id) and scope items whose quantities are
small formulas. Quantity uncertainty is propagated from the damage area's sigma.
"""

from __future__ import annotations

import ast
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from scan.damage import DamageRegion
from scan.measure import RoomMeasurement

__all__ = ["Flag", "ScopeItem", "RuleSet", "load_rules", "apply_rules", "DEFAULT_RULES"]

DEFAULT_RULES = Path(__file__).resolve().parents[2] / "rules" / "damage_rules.yaml"
_FUNCS = {"min": min, "max": max, "ceil": math.ceil, "sqrt": math.sqrt}
_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Load, ast.Call,
          ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub)


@dataclass
class Flag:
    id: int
    rule_id: str
    damage_id: int
    surface: str
    severity: str
    reason: str
    recommendation: str


@dataclass
class ScopeItem:
    id: int
    rule_id: str
    damage_id: int
    surface: str
    item: str
    qty: float
    qty_sigma: float
    unit: str


@dataclass
class RuleSet:
    version: int
    rules: list[dict]
    source: str


def safe_eval(expr: str, variables: dict) -> float:
    """Evaluate an arithmetic formula with whitelisted names and functions only."""
    tree = ast.parse(str(expr), mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _NODES):
            raise ValueError(f"not allowed in a quantity formula: {type(node).__name__} in {expr!r}")
        if isinstance(node, ast.Name) and node.id not in variables and node.id not in _FUNCS:
            raise ValueError(f"unknown name {node.id!r} in {expr!r}")
        if isinstance(node, ast.Call) and (not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS):
            raise ValueError(f"function not allowed in {expr!r}")
    return float(eval(compile(tree, "<qty>", "eval"), {"__builtins__": {}}, {**_FUNCS, **variables}))


def load_rules(path: Path = DEFAULT_RULES) -> RuleSet:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    rules = data.get("rules", [])
    ids = [r["id"] for r in rules]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate rule ids in " + str(path))
    return RuleSet(int(data.get("version", 1)), rules, str(path))


def _matches(when: dict, d: DamageRegion) -> bool:
    if "class" in when and d.cls not in when["class"]:
        return False
    if "surface" in when and d.surface_kind not in when["surface"]:
        return False
    checks = {"min_area_m2": d.area_m2 >= when.get("min_area_m2", -1),
              "max_area_m2": d.area_m2 <= when.get("max_area_m2", math.inf),
              "min_length_m": d.length_m >= when.get("min_length_m", -1),
              "max_length_m": d.length_m < when.get("max_length_m", math.inf)}
    if not all(checks.values()):
        return False
    if "max_bottom_m" in when and not (d.bottom_m is not None and d.bottom_m <= when["max_bottom_m"]):
        return False
    if "min_bottom_m" in when and not (d.bottom_m is not None and d.bottom_m > when["min_bottom_m"]):
        return False
    return True


def _variables(d: DamageRegion, room: RoomMeasurement | None, area: float) -> dict:
    height = (room.ceiling_height_m if room and room.ceiling_height_m else 2.5)
    wall_len = room.walls[d.wall_index].length_m if room and d.wall_index is not None else 0.0
    room_area = room.floor_area_m2 if room else 0.0
    surface_area = wall_len * height if d.surface_kind == "wall" else room_area
    return {"area": area, "length": d.length_m, "surface_area": surface_area, "wall_length": wall_len,
            "ceiling_height": height, "room_area": room_area}


def apply_rules(damage: list[DamageRegion], rooms: list[RoomMeasurement],
                ruleset: RuleSet | None = None) -> tuple[list[Flag], list[ScopeItem]]:
    ruleset = ruleset or load_rules()
    by_room = {r.id: r for r in rooms}
    flags: list[Flag] = []
    scope: list[ScopeItem] = []
    for d in damage:
        room = by_room.get(d.room_id)
        for rule in ruleset.rules:
            if not _matches(rule.get("when", {}), d):
                continue
            f = rule.get("flag")
            if f:
                flags.append(Flag(len(flags), rule["id"], d.id, d.surface, f.get("severity", "medium"),
                                  f.get("reason", ""), f.get("recommendation", "")))
            for s in rule.get("scope", []):
                v = _variables(d, room, d.area_m2)
                qty = safe_eval(s["qty"], v)
                hi = safe_eval(s["qty"], _variables(d, room, d.area_m2 + d.area_sigma_m2))
                lo = safe_eval(s["qty"], _variables(d, room, max(0.0, d.area_m2 - d.area_sigma_m2)))
                scope.append(ScopeItem(len(scope), rule["id"], d.id, d.surface, s["item"],
                                       round(qty, 3), round(abs(hi - lo) / 2, 3), s.get("unit", "")))
    return flags, scope


def to_dicts(flags: list[Flag], scope: list[ScopeItem]) -> dict:
    return {"flags": [asdict(f) for f in flags], "scope": [asdict(s) for s in scope]}
