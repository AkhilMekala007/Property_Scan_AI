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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
