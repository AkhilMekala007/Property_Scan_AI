"""Pipeline result -> validated ``Result`` (the published contract)."""

from __future__ import annotations

from datetime import datetime, timezone

from scan import __version__
from scan.contract import schema as S
from scan.contract.budget import INTERVAL_METHOD, Z90, budget_for, combine
from scan.core.devices import lookup_device


def measure(value: float, fit_sigma: float, sys_sigma: float, unit: str, *, lower_bound: bool = False,
            non_negative: bool = True) -> S.Measure:
    sigma = combine(fit_sigma, sys_sigma)
    lo = value - Z90 * sigma
    if non_negative:
        lo = max(0.0, lo)
    if lower_bound:  # e.g. a door head never seen: the value is only a minimum
        return S.Measure(value=round(value, 4), lo=round(value, 4), hi=None, sigma=round(sigma, 5), unit=unit,
                         bound="lower")
    return S.Measure(value=round(value, 4), lo=round(lo, 4), hi=round(value + Z90 * sigma, 4),
                     sigma=round(sigma, 5), unit=unit)


def _rooms(plan, tier: str, opening_ids_by_room: dict) -> list[S.Room]:
    b = budget_for(tier)
    out = []
    for r in plan.rooms:
        ceiling = None
        if r.ceiling_height_m is not None:
            ceiling = measure(r.ceiling_height_m, r.ceiling_sigma_m or 0.0, b.ceiling_m, "m")
        walls = [S.Wall(
            id=f"{r.name}.wall_{w.index}",
            index=w.index,
            length=measure(w.length_m, w.sigma_m, b.wall_abs_m + b.wall_rel * w.length_m, "m"),
            height=ceiling,
            observed=not w.inferred,
            coverage=min(1.0, max(0.0, w.coverage)),
            plane_ids=list(w.wall_ids),
        ) for w in r.walls]
        perimeter_sigma = combine(*[w.sigma_m for w in r.walls])
        out.append(S.Room(
            id=r.id, name=r.name, kind=r.kind,
            polygon_xz=[(float(x), float(z)) for x, z in r.corners_xz],
            floor_area=measure(r.floor_area_m2, r.floor_area_sigma_m2, r.perimeter_m * b.surface_offset_m, "m2"),
            perimeter=measure(r.perimeter_m, perimeter_sigma, b.wall_rel * r.perimeter_m + b.wall_abs_m, "m"),
            ceiling_height=ceiling,
            ceiling_note=r.ceiling_note,
            floor_tilt_deg=r.floor_tilt_deg,
            walls=walls,
            floor_id=f"{r.name}.floor",
            ceiling_id=f"{r.name}.ceiling",
            opening_ids=sorted(opening_ids_by_room.get(r.id, [])),
        ))
    return out


def _openings(plan, tier: str) -> list[S.Opening]:
    b = budget_for(tier)
    names = {r.id: r.name for r in plan.rooms}
    out = []
    for o in plan.openings:
        sill = None
        if o.kind == "window":
            sill = measure(o.sill_m, 0.0, b.opening_height_m, "m")
        out.append(S.Opening(
            id=o.id, type=o.kind, rooms=list(o.rooms),
            wall_ids=[f"{names[rid]}.wall_{k}" for rid, k in o.walls if rid in names],
            width=measure(o.width_m, o.width_sigma_m, b.opening_m, "m"),
            height=measure(o.height_m, 0.0, b.opening_height_m, "m", lower_bound=not o.head_observed),
            sill=sill,
            center_xz=(float(o.center_xz[0]), float(o.center_xz[1])),
            views=o.views, covered=o.covered, low_evidence=o.low_evidence,
        ))
    return out


def _property(plan, rooms: list[S.Room], tier: str) -> S.PropertyPlan:
    b = budget_for(tier)
    names = {r.id: r.name for r in plan.rooms}
    net_sigma = combine(*[r.floor_area.sigma for r in rooms])
    adjacency = []
    for a, bb, kind in plan.adjacency:
        oid = next((o.id for o in plan.openings if sorted(o.rooms) == sorted([a, bb]) and o.kind == kind), None)
        adjacency.append(S.Adjacency(room_a=a, room_b=bb, via=kind, opening_id=oid))
    shared = [S.SharedWall(room_a=s.room_a, wall_a=f"{names[s.room_a]}.wall_{s.wall_a}",
                           room_b=s.room_b, wall_b=f"{names[s.room_b]}.wall_{s.wall_b}",
                           thickness=measure(s.thickness_m, 0.0, 2 * b.surface_offset_m, "m"))
              for s in plan.shared_walls]
    return S.PropertyPlan(
        rooms_count=len(rooms),
        net_floor_area=measure(plan.net_area_m2, net_sigma, 0.0, "m2"),
        footprint_area=measure(plan.footprint_m2, net_sigma, 0.0, "m2"),
        connected=plan.connected_components == 1,
        adjacency=adjacency,
        shared_walls=shared,
        overlaps=[(int(a), int(c), float(m2)) for a, c, m2 in plan.overlaps],
        unmeasured_doorways=len(plan.unmeasured_doorways),
    )


def build_result(res, runtime_s: float) -> S.Result:
    from scan.damage.detect import DetectConfig
    from scan.models import MODELS
    from scan.semantics import SemanticsConfig

    meta = res.frameset.meta
    tier = meta.tier.value
    b = budget_for(tier)
    qc = res.qc.report
    plan = res.plan

    openings = _openings(plan, tier)
    by_room: dict[int, list[int]] = {}
    for o in openings:
        for rid in o.rooms:
            by_room.setdefault(rid, []).append(o.id)
    rooms = _rooms(plan, tier, by_room)

    damage = []
    for d in (res.damage.regions if res.damage else []):
        damage.append(S.Damage(
            id=d.id, surface_id=d.surface, room_id=d.room_id, cls=d.cls,
            area=measure(d.area_m2, d.area_sigma_m2, b.damage_rel * d.area_m2, "m2"),
            length=measure(d.length_m, 0.0, b.damage_length_m, "m"),
            extent_m=(float(d.extent_m[0]), float(d.extent_m[1])),
            bottom_above_floor_m=d.bottom_m,
            polygon_surface=[(float(a), float(c)) for a, c in d.outline_2d],
            confidence=d.confidence, views=d.n_views))

    device = lookup_device(meta.device_model)
    drift = res.drift
    used = [SemanticsConfig().model_key] + ([DetectConfig().model_key] if res.damage else [])
    return S.Result(
        capture=S.CaptureInfo(
            id=meta.capture_id, tier=tier, source_format=meta.source_format, device=meta.device_model,
            device_supported=device.supported if device else None, duration_s=meta.duration_s,
            frames_raw=meta.n_frames_raw, keyframes_used=len(res.labelled.frames),
            quality_score=qc.quality_score,
            issues=[S.QcIssue(code=i.code, severity=i.severity, message=i.message, fix=i.fix) for i in qc.issues],
            warnings=list(meta.warnings)),
        processing=S.Processing(
            pipeline_version=__version__,
            run_utc=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            runtime_s=round(runtime_s, 1),
            interval_method=INTERVAL_METHOD, calibrated=False,
            drift_correction=S.DriftInfo(
                enabled=drift.enabled, loops_accepted=drift.loops_accepted, loops_tested=drift.loops_tested,
                max_translation_m=drift.max_translation_m, max_yaw_deg=drift.max_yaw_deg, note=drift.note),
            models=[S.ModelUsed(key=k, repo=MODELS[k].repo_id, licence=MODELS[k].licence, purpose=MODELS[k].purpose)
                    for k in used]),
        property=_property(plan, rooms, tier),
        rooms=rooms,
        openings=openings,
        damage=damage,
        flags=[S.Flag(id=f.id, rule_id=f.rule_id, damage_id=f.damage_id, surface_id=f.surface,
                      severity=f.severity, reason=f.reason, recommendation=f.recommendation) for f in res.flags],
        scope=[S.ScopeItem(id=s.id, surface_id=s.surface, item=s.item,
                           quantity=measure(s.qty, s.qty_sigma, 0.0, s.unit), rule_id=s.rule_id,
                           damage_id=s.damage_id) for s in res.scope],
    )


def to_json(result: S.Result) -> str:
    return result.model_dump_json(indent=2, by_alias=True)
