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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
