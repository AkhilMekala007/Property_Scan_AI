"""Command line entry point: ``scan <command>``."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from scan.adapters.base import AdapterConfig
from scan.ingest import CaptureFormatError, load_capture
from scan.ingest.load import DEFAULT_CACHE_ROOT


def cmd_inspect(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    config = AdapterConfig(device_model=args.device)
    try:
        fs = load_capture(args.capture, cache_root=args.cache, config=config)
    except (CaptureFormatError, NotImplementedError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    load_s = time.perf_counter() - started
    m = fs.meta
    first = fs.frames[0] if fs.frames else None

    print(f"capture      {m.capture_id}")
    print(f"tier         {m.tier.value} ({m.source_format})")
    print(f"source       {m.source_dir}")
    print(f"device       {m.device_model or 'unknown'}")
    print(f"raw frames   {m.n_frames_raw}")
    if m.duration_s is not None:
        print(f"duration     {m.duration_s:.1f} s")
    print(f"keyframes    {len(fs)}")
    print(f"poses        {'yes' if fs.has_poses else 'no'}")
    print(f"depth        {'yes' if fs.has_depth else 'no'}")
    if first is not None:
        i = first.intrinsics
        print(f"rgb          {i.width}x{i.height}  fx={i.fx:.1f} fy={i.fy:.1f} cx={i.cx:.1f} cy={i.cy:.1f}")
        if first.depth_size:
            print(f"depth size   {first.depth_size[0]}x{first.depth_size[1]}")
    print(f"load time    {load_s:.1f} s")
    print(f"warnings     {len(m.warnings)}")
    for w in m.warnings:
        print(f"  - {w}")

    if args.topdown and fs.has_poses and fs.has_depth:
        from scan.debug.topdown import render_topdown

        out = Path(args.out) / m.capture_id / "debug_topdown.png"
        stats = render_topdown(fs, out)
        print(f"topdown      {out}")
        print(f"  camera height above floor {stats['camera_height_m']:.2f} m, "
              f"extent {stats['extent_m'][0]:.1f} x {stats['extent_m'][1]:.1f} m")
    return 0


def cmd_qc(args: argparse.Namespace) -> int:
    from scan.qc import run_qc, write_reports

    started = time.perf_counter()
    config = AdapterConfig(device_model=args.device)
    try:
        fs = load_capture(args.capture, cache_root=args.cache, config=config)
    except (CaptureFormatError, NotImplementedError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    report = run_qc(fs).report
    elapsed = time.perf_counter() - started
    json_path, md_path = write_reports(report, Path(args.out) / fs.meta.capture_id)

    c = report.coverage
    drops = report.drop_counts()
    print(f"QC  {report.capture_id}  ({report.tier})   quality {report.quality_score:.2f}")
    print(f"kept {report.n_frames_kept} / {report.n_frames_in} keyframes"
          + (f"   dropped: {', '.join(f'{n} {r}' for r, n in drops.items())}" if drops else ""))
    if report.n_rgb_blurry_kept:
        print(f"{report.n_rgb_blurry_kept} kept frames have blurry images (used for geometry only)")
    if c.floor_seen is not None:
        line = (f"floor seen {'yes' if c.floor_seen else 'NO'} ({c.floor_area_m2:.1f} m2)   "
                f"ceiling seen {'yes' if c.ceiling_seen else 'NO'} ({c.ceiling_area_m2 or 0:.1f} m2)")
        if c.camera_height_m is not None:
            line += f"   camera height {c.camera_height_m:.2f} m"
        print(line)
    print(f"median brightness {report.median_brightness:.0f}/255   time {elapsed:.1f} s")
    errors = [i for i in report.issues if i.severity == "error"]
    print(f"\nissues: {len(errors)} error(s), {len(report.issues) - len(errors)} other")
    icons = {"error": "x", "warning": "!", "info": "i"}
    for issue in report.issues:
        print(f" [{icons[issue.severity]}] {issue.code}: {issue.message}")
        if issue.fix:
            print(f"     fix: {issue.fix}")
    print(f"\nreport  {md_path}\n        {json_path}")
    return 1 if report.has_errors else 0


def cmd_labels(args: argparse.Namespace) -> int:
    import json

    from scan.models import WeightsMissingError
    from scan.qc import run_qc
    from scan.semantics import SemanticsConfig, run_semantics, write_overlays

    started = time.perf_counter()
    try:
        fs = load_capture(args.capture, cache_root=args.cache, config=AdapterConfig(device_model=args.device))
    except (CaptureFormatError, NotImplementedError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    fs = run_qc(fs).frameset
    cfg = SemanticsConfig(model_key=args.model, short_side=args.short_side, max_frames=args.max_frames)
    try:
        result = run_semantics(fs, cfg)
    except WeightsMissingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    elapsed = time.perf_counter() - started
    s = result.summary

    out_dir = Path(args.out) / fs.meta.capture_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "semantics_summary.json").write_text(json.dumps(s.to_dict(), indent=2), encoding="utf-8")

    print(f"LABELS  {s.capture_id}   model {s.model}")
    print(f"labelled {s.n_frames_labelled} frames ({s.n_frames_from_cache} from cache)   time {elapsed:.1f} s")
    shares = "  ".join(f"{k} {v:.0%}" for k, v in s.class_shares.items() if v >= 0.005)
    print(f"pixel shares   {shares}")
    print(f"mirror seen in {s.mirror_frames} frames")
    print("label vs geometry agreement:")
    for name, stats in s.agreement.items():
        value = "n/a" if stats["agreement"] is None else f"{stats['agreement']:.0%}"
        print(f"  {name:8s} {value:>5s}   ({stats['pixels']} px checked)")
    for issue in s.issues:
        print(f" [{'!' if issue.severity == 'warning' else 'i'}] {issue.code}: {issue.message}")
    if args.overlays:
        paths = write_overlays(result.frameset, out_dir / "labels", count=args.overlays)
        print(f"overlays  {out_dir / 'labels'}  ({len(paths)} images)")
    print(f"summary   {out_dir / 'semantics_summary.json'}")
    return 0


def cmd_structure(args: argparse.Namespace) -> int:
    from scan.qc import run_qc
    from scan.semantics import run_semantics
    from scan.structure import render_structure, run_structure, write_structure

    started = time.perf_counter()
    try:
        fs = load_capture(args.capture, cache_root=args.cache, config=AdapterConfig(device_model=args.device))
    except (CaptureFormatError, NotImplementedError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    qc = run_qc(fs)
    cov = qc.report.coverage
    labelled = run_semantics(qc.frameset).frameset
    model = run_structure(labelled, floor_hint_y=cov.floor_y, ceiling_seen=cov.ceiling_seen)
    elapsed = time.perf_counter() - started

    out_dir = Path(args.out) / fs.meta.capture_id
    json_path = write_structure(model, out_dir)
    img_path = render_structure(model, out_dir / "structure_debug.png")

    print(f"STRUCTURE  {model.capture_id}   voxels {model.n_voxels:,} at {model.voxel_size_m * 100:.0f} cm   "
          f"time {elapsed:.1f} s (fusion {model.timings_s['fuse']:.1f} s)")
    lc = model.label_counts
    print(f"voxel labels   wall {lc['wall']:,}  floor {lc['floor']:,}  ceiling {lc['ceiling']:,}  "
          f"door {lc['door']:,}  window {lc['window']:,}  other {lc['other']:,}  unknown {lc['unknown']:,}")
    print(f"removed        mirror {model.stats.n_mirror_voxels:,}  person {model.stats.n_person_voxels:,}")
    print(f"main wall direction {model.manhattan_deg:.1f} deg   wall directions found {model.stats.families_deg}")
    f = model.floor
    if f is not None:
        print(f"floor          tilt {f.tilt_deg:.2f} deg   fit spread {f.rms_m * 1000:.1f} mm   "
              f"area seen {f.area_m2:.1f} m2")
    heights = model.ceiling_heights()
    if heights:
        for k, (hgt, sig, area) in enumerate(heights):
            print(f"ceiling {k + 1}      {hgt:.3f} m above floor  ({area:.1f} m2; fit-only +-{sig * 1000:.2f} mm, excludes depth bias and drift)")
    else:
        print(f"ceiling        none ({model.ceiling_note})")
    s = model.stats
    share = s.n_wall_voxels_assigned / s.n_wall_voxels if s.n_wall_voxels else 0
    print(f"walls          {len(model.walls)} planes, {share:.0%} of wall voxels explained")
    for wall in sorted(model.walls, key=lambda w: -w.area_seen_m2)[: args.show]:
        print(f"  #{wall.id:<3d} dir {wall.angle_deg:6.1f} deg{' (snapped)' if wall.snapped else '          '}"
              f"  seen {wall.length_seen_m:4.2f} m x {wall.y_max - wall.y_min:4.2f} m  "
              f"coverage {wall.coverage:4.0%}  fit spread {wall.rms_m * 1000:4.1f} mm  "
              f"offset +-{wall.sigma_m * 1000:.2f} mm")
    print(f"json   {json_path}\nimage  {img_path}")
    return 0


def cmd_rooms(args: argparse.Namespace) -> int:
    from scan.qc import run_qc
    from scan.rooms import render_rooms, segment_rooms, write_rooms
    from scan.semantics import run_semantics
    from scan.structure import run_structure

    started = time.perf_counter()
    try:
        fs = load_capture(args.capture, cache_root=args.cache, config=AdapterConfig(device_model=args.device))
    except (CaptureFormatError, NotImplementedError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    qc = run_qc(fs)
    cov = qc.report.coverage
    labelled = run_semantics(qc.frameset).frameset
    model = run_structure(labelled, floor_hint_y=cov.floor_y, ceiling_seen=cov.ceiling_seen)
    t_rooms = time.perf_counter()
    layout = segment_rooms(model, fs.trajectory)
    rooms_s = time.perf_counter() - t_rooms
    elapsed = time.perf_counter() - started

    out_dir = Path(args.out) / fs.meta.capture_id
    json_path = write_rooms(layout, out_dir)
    img_path = render_rooms(layout, out_dir / "rooms_debug.png")

    total = sum(r.area_m2 for r in layout.rooms)
    print(f"ROOMS  {layout.capture_id}   {len(layout.rooms)} rooms, {len(layout.doorways)} doorways, "
          f"{total:.1f} m2 total   time {elapsed:.1f} s (segmentation {rooms_s:.1f} s)")
    for r in layout.rooms:
        ceiling = f"ceiling {r.ceiling_height_m:.3f} m" if r.ceiling_height_m is not None else f"ceiling n/a ({r.ceiling_note})"
        print(f"  {r.name:12s} {r.area_m2:6.1f} m2   {ceiling}   walls {len(r.wall_ids):2d}   doorways {r.doorway_ids}")
    for d in layout.doorways:
        print(f"  doorway {d.id}: {layout.rooms[d.room_a - 1].name} <-> {layout.rooms[d.room_b - 1].name}  "
              f"{d.kind:7s} ~{d.width_m:.2f} m")
    if layout.unreached_areas_m2:
        print(f"  dropped {len(layout.unreached_areas_m2)} area(s) seen only from outside: "
              f"{', '.join(f'{a:.1f} m2' for a in layout.unreached_areas_m2)}")
    print(f"json   {json_path}\nimage  {img_path}")
    return 0


def cmd_measure(args: argparse.Namespace) -> int:
    from scan.measure import measure_rooms, render_plan, write_measurements
    from scan.qc import run_qc
    from scan.rooms import segment_rooms
    from scan.semantics import run_semantics
    from scan.structure import run_structure

    started = time.perf_counter()
    try:
        fs = load_capture(args.capture, cache_root=args.cache, config=AdapterConfig(device_model=args.device))
    except (CaptureFormatError, NotImplementedError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    qc = run_qc(fs)
    cov = qc.report.coverage
    labelled = run_semantics(qc.frameset).frameset
    model = run_structure(labelled, floor_hint_y=cov.floor_y, ceiling_seen=cov.ceiling_seen)
    layout = segment_rooms(model, fs.trajectory)
    rooms, diag = measure_rooms(model, layout)
    elapsed = time.perf_counter() - started

    out_dir = Path(args.out) / fs.meta.capture_id
    json_path = write_measurements(rooms, diag, fs.meta.capture_id, out_dir)
    img_path = render_plan(rooms, layout, out_dir / "plan.png")

    total = sum(r.floor_area_m2 for r in rooms)
    print(f"MEASURE  {fs.meta.capture_id}   {len(rooms)} rooms, {total:.2f} m2 total   time {elapsed:.1f} s")
    print("(uncertainties are fit-only; calibration adds depth bias and drift)")
    for r in rooms:
        ceiling = (f"ceiling {r.ceiling_height_m:.3f} m +-{r.ceiling_sigma_m * 1000:.1f} mm"
                   if r.ceiling_height_m is not None else f"ceiling n/a ({r.ceiling_note})")
        print(f"\n  {r.name}  ({r.kind})   area {r.floor_area_m2:.2f} m2 +-{r.floor_area_sigma_m2:.3f}   "
              f"perimeter {r.perimeter_m:.2f} m   {ceiling}   floor tilt {r.floor_tilt_deg:.2f} deg")
        for wm in r.walls:
            flag = "  INFERRED" if wm.inferred else ""
            print(f"    wall {wm.index:2d}  {wm.length_m:5.2f} m +-{wm.sigma_m * 1000:5.1f} mm   "
                  f"seen {wm.coverage:4.0%}   planes {wm.wall_ids}{flag}")
    if diag["walls_not_axis_aligned"]:
        print(f"\n  note: {diag['walls_not_axis_aligned']} wall planes are not right-angled and were not used")
    print(f"\njson   {json_path}\nplan   {img_path}")
    return 0


def cmd_openings(args: argparse.Namespace) -> int:
    from scan.measure import measure_rooms, render_plan
    from scan.openings import find_openings, write_openings
    from scan.qc import run_qc
    from scan.rooms import segment_rooms
    from scan.semantics import run_semantics
    from scan.structure import run_structure

    started = time.perf_counter()
    try:
        fs = load_capture(args.capture, cache_root=args.cache, config=AdapterConfig(device_model=args.device))
    except (CaptureFormatError, NotImplementedError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    qc = run_qc(fs)
    cov = qc.report.coverage
    labelled = run_semantics(qc.frameset).frameset
    model = run_structure(labelled, floor_hint_y=cov.floor_y, ceiling_seen=cov.ceiling_seen)
    layout = segment_rooms(model, fs.trajectory)
    rooms, _ = measure_rooms(model, layout)
    out_dir = Path(args.out) / fs.meta.capture_id
    t0 = time.perf_counter()
    openings = find_openings(model, layout, rooms, labelled,
                             debug_dir=out_dir / "elevations" if args.elevations else None)
    openings_s = time.perf_counter() - t0
    elapsed = time.perf_counter() - started

    json_path = write_openings(openings, fs.meta.capture_id, out_dir)
    img_path = render_plan(rooms, layout, out_dir / "plan_openings.png", openings=openings)
    counts = {k: sum(o.kind == k for o in openings) for k in ("door", "window", "opening")}
    print(f"OPENINGS  {fs.meta.capture_id}   {counts['door']} doors, {counts['window']} windows, "
          f"{counts['opening']} open passages   time {elapsed:.1f} s (openings {openings_s:.1f} s)")
    print("(widths from depth only; uncertainties fit-only)")
    for room in rooms:
        mine = [o for o in openings if o.room_id == room.id]
        if not mine:
            continue
        print(f"\n  {room.name}")
        for o in mine:
            to = f" -> {rooms[o.connects_room - 1].name}" if o.connects_room else ""
            height = f"top {o.height_m:.2f} m" if o.head_observed else f"top >{o.height_m:.2f} m (unseen)"
            if o.kind == "window":
                height += f", sill {o.sill_m:.2f} m"
            print(f"    {o.kind:8s} wall {o.wall_index:2d}  width {o.width_m:.3f} m +-{o.width_sigma_m * 1000:4.1f} mm  "
                  f"{height}   jambs seen {o.jambs_observed}/2   rays {o.evidence['through_rays']}"
                  f"{'   COVERED' if o.covered else ''}{'   LOW EVIDENCE' if o.low_evidence else ''}{to}")
    print(f"\njson   {json_path}\nplan   {img_path}")
    if args.elevations:
        print(f"walls  {out_dir / 'elevations'}")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    from scan.stitch import write_plan
    from scan.stitch.render import render_floor_plan

    started = time.perf_counter()
    try:
        fs = load_capture(args.capture, cache_root=args.cache, config=AdapterConfig(device_model=args.device))
    except (CaptureFormatError, NotImplementedError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    from scan.pipeline import run_pipeline

    res = run_pipeline(fs, drift=not args.no_drift_fix)
    plan, layout = res.plan, res.layout
    elapsed = time.perf_counter() - started

    out_dir = Path(args.out) / fs.meta.capture_id
    json_path = write_plan(plan, out_dir)
    img_path = render_floor_plan(plan, layout.floor_map.frame, out_dir / "floor_plan.png")
    names = {r.id: r.name for r in plan.rooms}

    print(f"PLAN  {plan.capture_id}   {len(plan.rooms)} rooms   net {plan.net_area_m2:.2f} m2   "
          f"footprint {plan.footprint_m2:.2f} m2   time {elapsed:.1f} s")
    d = res.drift
    if d.enabled:
        print(f"drift fix: {d.loops_accepted} loop closure(s) of {d.loops_tested} tested, "
              f"max correction {d.max_translation_m * 100:.1f} cm / {d.max_yaw_deg:.2f} deg"
              + (f"  ({d.note})" if d.note else ""))
    else:
        print("drift fix: OFF")
    print(f"shared walls {len(plan.shared_walls)}   median wall thickness "
          f"{plan.diagnostics['median_wall_thickness_m'] * 100:.1f} cm")
    print(f"openings {len(plan.openings)} "
          f"({sum(o.views == 2 for o in plan.openings)} measured from both sides, "
          f"{sum(o.views_agree is False for o in plan.openings)} disagreeing)")
    print(f"connected: {'yes' if plan.connected_components == 1 else f'NO ({plan.connected_components} groups)'}   "
          f"overlaps: {'none' if not plan.overlaps else plan.overlaps}")
    if plan.adjusted_edges:
        print(f"moved {len(plan.adjusted_edges)} inferred edge(s) out of neighbours: "
              + ", ".join(f"{names[r]} edge {k} by {m:.2f} m" for r, k, m in plan.adjusted_edges))
    if plan.unmeasured_doorways:
        print(f"doorways with no measured opening: {plan.unmeasured_doorways}")
    print("\nadjacency")
    for a, b, kind in plan.adjacency:
        print(f"  {names[a]} <-> {names[b]}  ({kind})")
    print("\nopenings")
    for o in plan.openings:
        where = " <-> ".join(names[r] for r in o.rooms)
        both = f"  views agree: {o.views_agree}" if o.views == 2 else ""
        print(f"  {o.kind:8s} {where:28s} width {o.width_m:.3f} m +-{o.width_sigma_m * 1000:4.1f} mm{both}")
    print(f"\njson   {json_path}\nplan   {img_path}")
    return 0 if not plan.overlaps else 1


def cmd_drift(args: argparse.Namespace) -> int:
    """Drift ablation: the whole pipeline with correction off and on, compared side by side."""
    import json

    from scan.pipeline import consistency_metrics, run_pipeline
    from scan.stitch.render import render_floor_plan

    try:
        fs = load_capture(args.capture, cache_root=args.cache, config=AdapterConfig(device_model=args.device))
    except (CaptureFormatError, NotImplementedError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    started = time.perf_counter()
    off = run_pipeline(fs, drift=False)
    on = run_pipeline(fs, drift=True)
    m_off, m_on = consistency_metrics(off), consistency_metrics(on)
    out_dir = Path(args.out) / fs.meta.capture_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "drift_ablation.json").write_text(json.dumps(
        {"off": m_off, "on": m_on, "drift_report": on.drift.to_dict()}, indent=2), encoding="utf-8")
    render_floor_plan(off.plan, off.layout.floor_map.frame, out_dir / "floor_plan_drift_off.png",
                      title=f"{fs.meta.capture_id}  drift correction OFF")
    render_floor_plan(on.plan, on.layout.floor_map.frame, out_dir / "floor_plan_drift_on.png",
                      title=f"{fs.meta.capture_id}  drift correction ON")
    overlay = _footprint_overlay(off, on, out_dir / "drift_ablation.png")

    d = on.drift
    print(f"DRIFT ABLATION  {fs.meta.capture_id}   time {time.perf_counter() - started:.1f} s")
    print(f"correction: {d.loops_accepted} loop closure(s) accepted of {d.loops_tested} tested, "
          f"{d.loops_pruned} pruned, max {d.max_translation_m * 100:.1f} cm / {d.max_yaw_deg:.2f} deg"
          + (f"  ({d.note})" if d.note else ""))
    print()
    print(f"  {'metric':38s} {'OFF':>10s} {'ON':>10s}")
    for k in m_off:
        print(f"  {k:38s} {str(m_off[k]):>10s} {str(m_on[k]):>10s}")
    print()
    print(f"json     {out_dir / 'drift_ablation.json'}")
    print(f"overlay  {overlay}")
    return 0


def cmd_repeat(args: argparse.Namespace) -> int:
    """Repeatability: rerun the pipeline under tiny pose perturbations and report output spread."""
    import json

    from scan.pipeline import run_pipeline
    from scan.repeat import compare, perturb, summarise

    try:
        fs = load_capture(args.capture, cache_root=args.cache, config=AdapterConfig(device_model=args.device))
    except (CaptureFormatError, NotImplementedError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    started = time.perf_counter()
    summaries = []
    for k in range(args.runs):
        run_fs = fs if k == 0 else perturb(fs, seed=k, trans_sigma_m=args.trans_mm / 1000, yaw_sigma_deg=args.yaw_deg)
        summaries.append(summarise(run_pipeline(run_fs, drift=False)))
        print(f"  run {k + 1}/{args.runs}: {summaries[-1].n_rooms} rooms, {summaries[-1].n_openings} openings",
              flush=True)
    report = compare(summaries)
    head = report.headline()
    out_dir = Path(args.out) / fs.meta.capture_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "repeatability.json").write_text(json.dumps(
        {"perturbation": {"trans_mm": args.trans_mm, "yaw_deg": args.yaw_deg}, "headline": head,
         "room_area_spread_pct": report.room_area_spread_pct, "wall_length_spread_m": report.wall_length_spread_m,
         "opening_width_spread_m": report.opening_width_spread_m}, indent=2), encoding="utf-8")
    print(f"REPEAT  {fs.meta.capture_id}   {args.runs} runs, pose noise {args.trans_mm} mm / {args.yaw_deg} deg "
          f"per fragment   time {time.perf_counter() - started:.1f} s")
    for k, v in head.items():
        print(f"  {k:32s} {v}")
    return 0


def cmd_damage(args: argparse.Namespace) -> int:
    import json
    from dataclasses import asdict

    from scan.pipeline import run_pipeline
    from scan.rules import to_dicts

    started = time.perf_counter()
    try:
        fs = load_capture(args.capture, cache_root=args.cache, config=AdapterConfig(device_model=args.device))
    except (CaptureFormatError, NotImplementedError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    res = run_pipeline(fs, upto="damage", drift=not args.no_drift_fix)
    d = res.damage
    out_dir = Path(args.out) / fs.meta.capture_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "damage.json").write_text(json.dumps(
        {"damage": [asdict(r) for r in d.regions], **to_dicts(res.flags, res.scope),
         "diagnostics": {"frames_scanned": d.frames_scanned, "frames_from_cache": d.frames_from_cache,
                         "detections": d.detections, "views_measured": d.views_measured, "dropped": d.dropped}},
        indent=2), encoding="utf-8")
    print(f"DAMAGE  {fs.meta.capture_id}   {len(d.regions)} region(s), {len(res.flags)} flag(s), "
          f"{len(res.scope)} scope item(s)   time {time.perf_counter() - started:.1f} s")
    print(f"scanned {d.frames_scanned} frames ({d.frames_from_cache} from cache): {d.detections} detections above "
          f"threshold, {d.views_measured} measured on a surface; dropped {d.dropped or 'none'}")
    for r in d.regions:
        print(f"  #{r.id} {r.cls:11s} on {r.surface:18s} area {r.area_m2:.3f} m2 +-{r.area_sigma_m2:.3f}  "
              f"length {r.length_m:.2f} m  views {r.n_views}  confidence {r.confidence:.2f}")
    for f in res.flags:
        print(f"  FLAG [{f.severity}] {f.rule_id} on {f.surface}: {f.reason}")
    for s in res.scope:
        print(f"  SCOPE {s.item}: {s.qty:.2f} +-{s.qty_sigma:.2f} {s.unit}  ({s.surface}, {s.rule_id})")
    print(f"json   {out_dir / 'damage.json'}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """One command per capture: the full pipeline -> result.json (published schema) + plan.png."""
    from scan.contract import build_result, to_json
    from scan.pipeline import run_pipeline
    from scan.qc import write_reports
    from scan.stitch.render import render_floor_plan

    started = time.perf_counter()
    try:
        fs = load_capture(args.capture, cache_root=args.cache, config=AdapterConfig(device_model=args.device))
    except (CaptureFormatError, NotImplementedError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    res = run_pipeline(fs, upto="damage", drift=not args.no_drift_fix)
    result = build_result(res, time.perf_counter() - started)
    out_dir = Path(args.out) / fs.meta.capture_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "result.json").write_text(to_json(result), encoding="utf-8")
    write_reports(res.qc.report, out_dir)
    render_floor_plan(res.plan, res.layout.floor_map.frame, out_dir / "plan.png",
                      damage=res.damage.regions if res.damage else None)

    p, c = result.property, result.capture
    print(f"RESULT  {c.id}   tier {c.tier}   quality {c.quality_score:.2f}   {result.processing.runtime_s:.0f} s")
    print(f"  {p.rooms_count} rooms, net {p.net_floor_area.value:.2f} m2 "
          f"[{p.net_floor_area.lo:.2f}, {p.net_floor_area.hi:.2f}], connected {p.connected}, "
          f"overlaps {len(p.overlaps)}")
    for r in result.rooms:
        ceiling = (f"ceiling {r.ceiling_height.value:.3f} m [{r.ceiling_height.lo:.3f}, {r.ceiling_height.hi:.3f}]"
                   if r.ceiling_height else f"ceiling n/a ({r.ceiling_note})")
        seen = sum(w.observed for w in r.walls)
        print(f"  {r.name:12s} {r.floor_area.value:6.2f} m2   {ceiling}   walls {seen}/{len(r.walls)} observed")
    print(f"  openings {len(result.openings)}, damage {len(result.damage)}, flags {len(result.flags)}, "
          f"scope items {len(result.scope)}")
    errors = [i for i in c.issues if i.severity == "error"]
    for i in errors:
        print(f"  [x] {i.code}: {i.message}")
    print(f"json   {out_dir / 'result.json'}  (schema {result.schema_version}, intervals uncalibrated)")
    print(f"plan   {out_dir / 'plan.png'}")
    return 1 if errors or p.overlaps else 0


def cmd_schema(args: argparse.Namespace) -> int:
    import json

    from scan.contract import json_schema

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(json_schema(), indent=2) + chr(10), encoding="utf-8")
    print(f"schema written to {out}")
    return 0


def _footprint_overlay(off, on, out_path):
    """Room outlines from both runs on one canvas in world coordinates: red = off, green = on."""
    import cv2
    import numpy as np

    polys_off = [np.array(r.corners_xz) for r in off.plan.rooms]
    polys_on = [np.array(r.corners_xz) for r in on.plan.rooms]
    allp = np.concatenate(polys_off + polys_on)
    lo = allp.min(axis=0) - 0.5
    hi = allp.max(axis=0) + np.array([0.5, 1.0])
    ppm = 100
    w, h = (np.ceil((hi - lo) * ppm)).astype(int)
    img = np.full((h, w, 3), 255, np.uint8)
    for polys, colour, thick in ((polys_off, (60, 60, 220), 3), (polys_on, (40, 160, 40), 1)):
        for p in polys:
            pts = np.round((p - lo) * ppm).astype(np.int32)
            cv2.polylines(img, [pts], True, colour, thick, cv2.LINE_AA)
    cv2.putText(img, "red (thick) = drift correction OFF   green (thin) = ON", (10, h - 15),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
    cv2.imwrite(str(out_path), img)
    return out_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scan", description="Property Scan AI pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("inspect", help="detect a capture's tier, load it and print a summary")
    p.add_argument("capture", help="capture folder (Stray export, video, or photo folders)")
    p.add_argument("--device", help="iPhone model, when the capture files don't record it")
    p.add_argument("--cache", default=str(DEFAULT_CACHE_ROOT), help="frame cache folder")
    p.add_argument("--out", default="outputs", help="output folder for debug images")
    p.add_argument("--topdown", action="store_true", help="also save a top-down debug render")
    p.set_defaults(func=cmd_inspect)

    q = sub.add_parser("qc", help="quality-check a capture and write qc_report.md/json")
    q.add_argument("capture", help="capture folder")
    q.add_argument("--device", help="iPhone model, when the capture files don't record it")
    q.add_argument("--cache", default=str(DEFAULT_CACHE_ROOT), help="frame cache folder")
    q.add_argument("--out", default="outputs", help="output folder for reports")
    q.set_defaults(func=cmd_qc)

    lb = sub.add_parser("labels", help="label wall/floor/ceiling/door/window/mirror in each frame")
    lb.add_argument("capture", help="capture folder")
    lb.add_argument("--device", help="iPhone model, when the capture files don't record it")
    lb.add_argument("--model", default="segformer-b2-ade", help="segmentation model key (see scan/models.py)")
    lb.add_argument("--short-side", type=int, default=320, help="model input size (shorter image side, px)")
    lb.add_argument("--max-frames", type=int, default=100, help="most frames to label per capture")
    lb.add_argument("--overlays", type=int, default=6, help="number of overlay images to save (0 = none)")
    lb.add_argument("--cache", default=str(DEFAULT_CACHE_ROOT), help="frame cache folder")
    lb.add_argument("--out", default="outputs", help="output folder")
    lb.set_defaults(func=cmd_labels)

    st = sub.add_parser("structure", help="fuse frames and find floor, ceiling and wall planes")
    st.add_argument("capture", help="capture folder")
    st.add_argument("--device", help="iPhone model, when the capture files don't record it")
    st.add_argument("--show", type=int, default=12, help="number of largest walls to list")
    st.add_argument("--cache", default=str(DEFAULT_CACHE_ROOT), help="frame cache folder")
    st.add_argument("--out", default="outputs", help="output folder")
    st.set_defaults(func=cmd_structure)

    rm = sub.add_parser("rooms", help="split the capture into rooms and doorways")
    rm.add_argument("capture", help="capture folder")
    rm.add_argument("--device", help="iPhone model, when the capture files don't record it")
    rm.add_argument("--cache", default=str(DEFAULT_CACHE_ROOT), help="frame cache folder")
    rm.add_argument("--out", default="outputs", help="output folder")
    rm.set_defaults(func=cmd_rooms)

    ms = sub.add_parser("measure", help="measure each room: wall lengths, floor area, ceiling height")
    ms.add_argument("capture", help="capture folder")
    ms.add_argument("--device", help="iPhone model, when the capture files don't record it")
    ms.add_argument("--cache", default=str(DEFAULT_CACHE_ROOT), help="frame cache folder")
    ms.add_argument("--out", default="outputs", help="output folder")
    ms.set_defaults(func=cmd_measure)

    op = sub.add_parser("openings", help="find doors, windows and open passages with their widths")
    op.add_argument("capture", help="capture folder")
    op.add_argument("--device", help="iPhone model, when the capture files don't record it")
    op.add_argument("--elevations", action="store_true", help="save a front-view debug image of every wall")
    op.add_argument("--cache", default=str(DEFAULT_CACHE_ROOT), help="frame cache folder")
    op.add_argument("--out", default="outputs", help="output folder")
    op.set_defaults(func=cmd_openings)

    pl = sub.add_parser("plan", help="stitch rooms into one whole-property floor plan")
    pl.add_argument("capture", help="capture folder")
    pl.add_argument("--device", help="iPhone model, when the capture files don't record it")
    pl.add_argument("--cache", default=str(DEFAULT_CACHE_ROOT), help="frame cache folder")
    pl.add_argument("--out", default="outputs", help="output folder")
    pl.add_argument("--no-drift-fix", action="store_true", help="use ARKit poses as-is (for the ablation)")
    pl.set_defaults(func=cmd_plan)

    dr = sub.add_parser("drift", help="drift ablation: full pipeline with correction off vs on")
    dr.add_argument("capture", help="capture folder")
    dr.add_argument("--device", help="iPhone model, when the capture files don't record it")
    dr.add_argument("--cache", default=str(DEFAULT_CACHE_ROOT), help="frame cache folder")
    dr.add_argument("--out", default="outputs", help="output folder")
    dr.set_defaults(func=cmd_drift)

    rp = sub.add_parser("repeat", help="repeatability under tiny pose perturbations")
    rp.add_argument("capture", help="capture folder")
    rp.add_argument("--runs", type=int, default=3)
    rp.add_argument("--trans-mm", type=float, default=5.0, help="per-fragment translation noise (mm)")
    rp.add_argument("--yaw-deg", type=float, default=0.1, help="per-fragment yaw noise (deg)")
    rp.add_argument("--device", help="iPhone model, when the capture files don't record it")
    rp.add_argument("--cache", default=str(DEFAULT_CACHE_ROOT), help="frame cache folder")
    rp.add_argument("--out", default="outputs", help="output folder")
    rp.set_defaults(func=cmd_repeat)

    dm = sub.add_parser("damage", help="detect and measure damage, then apply flag/scope rules")
    dm.add_argument("capture", help="capture folder")
    dm.add_argument("--device", help="iPhone model, when the capture files don't record it")
    dm.add_argument("--no-drift-fix", action="store_true")
    dm.add_argument("--cache", default=str(DEFAULT_CACHE_ROOT), help="frame cache folder")
    dm.add_argument("--out", default="outputs", help="output folder")
    dm.set_defaults(func=cmd_damage)

    rn = sub.add_parser("run", help="ONE COMMAND PER CAPTURE: full pipeline -> result.json + plan.png")
    rn.add_argument("capture", help="capture folder")
    rn.add_argument("--device", help="iPhone model, when the capture files don't record it")
    rn.add_argument("--no-drift-fix", action="store_true")
    rn.add_argument("--cache", default=str(DEFAULT_CACHE_ROOT), help="frame cache folder")
    rn.add_argument("--out", default="outputs", help="output folder")
    rn.set_defaults(func=cmd_run)

    sc = sub.add_parser("schema", help="write the published JSON schema of result.json")
    sc.add_argument("--out", default="schema/result.schema.json")
    sc.set_defaults(func=cmd_schema)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
