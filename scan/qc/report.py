"""QC data types, thresholds and report writers."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

INFO, WARNING, ERROR = "info", "warning", "error"


@dataclass(frozen=True)
class QcConfig:
    # per-frame drop rules
    min_sharpness_rel: float = 0.4  # vs the median of neighbouring keyframes
    sharpness_window: int = 4  # neighbours on each side
    max_speed_mps: float = 1.5
    max_angular_dps: float = 120.0
    max_dark_frac: float = 0.6  # share of near-black pixels
    max_bright_frac: float = 0.6  # share of blown-out pixels
    min_depth_valid_frac: float = 0.3
    jump_speed_mps: float = 3.0  # raw step faster than this = tracking jump
    jump_guard_s: float = 0.5  # drop keyframes this close to a jump
    # never drop below this
    min_keep_frac: float = 0.5
    min_keep_frames: int = 30
    # capture-level
    low_light_brightness: float = 50.0  # median of frame mean brightness, 0-255
    min_duration_s: float = 10.0
    min_surface_area_m2: float = 2.0  # floor / ceiling must be seen over this area
    ceiling_min_above_floor_m: float = 2.0
    fast_motion_warn_frac: float = 0.15


@dataclass
class FrameQuality:
    index: int
    timestamp: float
    sharpness: float
    sharpness_rel: float = 1.0
    brightness: float = 0.0
    dark_frac: float = 0.0
    bright_frac: float = 0.0
    depth_valid_frac: float | None = None
    high_conf_frac: float | None = None
    speed_mps: float | None = None
    angular_dps: float | None = None
    pitch_deg: float | None = None  # optical axis elevation, + looks up
    kept: bool = True
    reasons: list[str] = field(default_factory=list)  # why the frame was dropped
    rgb_ok: bool = True  # False: image too blurry for RGB-based steps (frame may still be kept)


@dataclass
class Issue:
    code: str
    severity: str
    message: str
    fix: str | None = None


@dataclass
class Coverage:
    floor_seen: bool | None = None
    floor_area_m2: float | None = None
    floor_y: float | None = None
    ceiling_seen: bool | None = None
    ceiling_area_m2: float | None = None
    camera_height_m: float | None = None
    up_frames_frac: float | None = None


@dataclass
class QualityReport:
    capture_id: str
    tier: str
    n_frames_in: int
    n_frames_kept: int
    quality_score: float
    median_brightness: float
    coverage: Coverage
    issues: list[Issue]
    frames: list[FrameQuality]
    tracking_jumps_s: list[float] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(i.severity == ERROR for i in self.issues)

    @property
    def n_rgb_blurry_kept(self) -> int:
        return sum(1 for f in self.frames if f.kept and not f.rgb_ok)

    def drop_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for f in self.frames:
            if not f.kept:
                for r in f.reasons:
                    counts[r] = counts.get(r, 0) + 1
        return counts

    def to_dict(self) -> dict:
        return asdict(self)


def write_reports(report: QualityReport, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "qc_report.json"
    md_path = out_dir / "qc_report.md"
    json_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


def _fmt(value, spec: str, missing: str = "n/a") -> str:
    return missing if value is None else format(value, spec)


def render_markdown(r: QualityReport) -> str:
    c = r.coverage
    drops = r.drop_counts()
    lines = [
        f"# QC report: {r.capture_id}",
        "",
        "| | |",
        "|---|---|",
        f"| Tier | {r.tier} |",
        f"| Quality score | {r.quality_score:.2f} |",
        f"| Keyframes kept | {r.n_frames_kept} / {r.n_frames_in} |",
        f"| Kept, but image blurry (geometry only) | {r.n_rgb_blurry_kept} |",
        f"| Median brightness | {r.median_brightness:.0f} / 255 |",
        f"| Floor seen | {c.floor_seen} ({_fmt(c.floor_area_m2, '.1f')} m²) |",
        f"| Ceiling seen | {c.ceiling_seen} ({_fmt(c.ceiling_area_m2, '.1f')} m²) |",
        f"| Camera height above floor | {_fmt(c.camera_height_m, '.2f')} m |",
        f"| Frames looking up | {_fmt(c.up_frames_frac, '.0%')} |",
        f"| Tracking jumps | {len(r.tracking_jumps_s)} |",
        "",
        "## Issues",
        "",
    ]
    if not r.issues:
        lines.append("None.")
    for i in r.issues:
        lines.append(f"- **{i.severity.upper()} `{i.code}`**: {i.message}")
        if i.fix:
            lines.append(f"  - Fix: {i.fix}")
    lines += ["", "## Dropped frames by reason", ""]
    if not drops:
        lines.append("None.")
    for reason, n in sorted(drops.items(), key=lambda kv: -kv[1]):
        lines.append(f"- {reason}: {n}")
    return "\n".join(lines) + "\n"
