"""C5 Drift correction: fragment pose graph with ICP loop closures, gravity kept exact.

ARKit poses are accurate over short stretches but drift over a long multi-room
walk. The walk is cut into fragments; consecutive fragments are trusted as
ARKit says, fragments that revisit the same place are aligned with ICP, and a
pose graph distributes the error. Corrections are restricted to a rotation
about the vertical axis plus a translation, because ARKit's gravity is accurate
and floors must stay level.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace

import numpy as np

from scan.core.geometry import backproject, transform_points
from scan.core.types import FrameSet, Trajectory

__all__ = ["DriftConfig", "DriftReport", "correct_drift"]


@dataclass(frozen=True)
class DriftConfig:
    fragment_size: int = 20  # keyframes per fragment
    voxel_m: float = 0.03
    depth_stride: int = 3
    loop_max_dist_m: float = 3.0  # fragments whose camera centroids are this close may share a view
    min_gap_fragments: int = 2  # neighbours in time are odometry, not loop closures
    icp_max_corr_m: float = 0.06
    icp_scales_m: tuple[float, ...] = (0.06, 0.03, 0.015)  # coarse-to-fine ICP
    eval_corr_m: float = 0.02  # loop closures are judged at this tight window
    min_tight_fitness: float = 0.30
    min_fitness_gain: float = 0.02  # the alignment must genuinely improve the tight overlap
    min_constraint_ratio: float = 0.05  # horizontal translation pinned in both directions (no sliding)
    max_loop_correction_m: float = 0.30  # an ICP result moving more than this is not drift
    max_loop_correction_deg: float = 3.0
    prune_threshold: float = 0.25  # Open3D line-process threshold for rejecting loop edges
    odometry_stiffness: float = 1e5  # minimum information on consecutive-fragment links
    # plane-anchored heading correction (used when no loop closure is available)
    heading_min_wall_pts: int = 300  # vertical-surface points a fragment needs to measure its heading
    heading_max_deg: float = 3.0  # larger apparent deviations are not drift (e.g. a non-square wall)
    # Apply the heading correction only above this drift. Below it the wall-length error it can remove is
    # < 1 mm (L * (1 - cos 1 deg) = 0.5 mm on 3.5 m), smaller than the outline changes any pose change
    # triggers in room assembly (3BHK ablation: walls in gate 4/7 -> 2/7 at 0.85 deg). Chosen after that
    # ablation; documented in docs/LLD/09_drift.md. The drift ablation forces it on.
    heading_apply_min_deg: float = 1.0
    force_heading: bool = False


@dataclass
class FragmentCorrection:
    fragment: int
    first_keyframe: int
    n_keyframes: int
    translation_m: float
    yaw_deg: float


@dataclass
class DriftReport:
    enabled: bool
    n_fragments: int = 0
    loops_tested: int = 0
    loops_accepted: int = 0
    loops_pruned: int = 0
    max_translation_m: float = 0.0
    max_yaw_deg: float = 0.0
    corrections: list[FragmentCorrection] = field(default_factory=list)
    note: str | None = None
    # every tested revisit: overlap before/after ICP at the tight window, the shift ICP proposed, outcome
    checks: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _yaw_only(T: np.ndarray) -> np.ndarray:
    """Keep a rotation about world +y and the translation; drop roll and pitch."""
    R = T[:3, :3]
    yaw = np.arctan2(R[0, 2] - R[2, 0], R[0, 0] + R[2, 2])
    c, s = np.cos(yaw), np.sin(yaw)
    out = np.eye(4)
    out[:3, :3] = [[c, 0, s], [0, 1, 0], [-s, 0, c]]
    out[:3, 3] = T[:3, 3]
    return out


def _yaw_deg(T: np.ndarray) -> float:
    R = T[:3, :3]
    return float(np.degrees(np.arctan2(R[0, 2] - R[2, 0], R[0, 0] + R[2, 2])))


def _fragment_cloud(frames, cfg: DriftConfig):
    import open3d as o3d

    pts = []
    for f in frames:
        depth, conf = f.depth(), f.confidence()
        if depth is None or f.T_world_cam is None:
            continue
        p = backproject(depth, f.depth_intrinsics, mask=(conf == 2) if conf is not None else None,
                        stride=cfg.depth_stride)
        pts.append(transform_points(f.T_world_cam, p))
    if not pts:
        return None
    pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(np.concatenate(pts)))
    pcd = pcd.voxel_down_sample(cfg.voxel_m)
    pcd.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=cfg.voxel_m * 4, max_nn=30))
    return pcd


def _yaw_matrix(phi: float) -> np.ndarray:
    c, s = np.cos(phi), np.sin(phi)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def _wall_heading(pcd, cfg: DriftConfig) -> tuple[float | None, float]:
    """Dominant wall direction of a fragment, modulo 90 deg (radians), and its weight.

    Walls of a building are parallel or perpendicular, so wall normals cluster at four angles;
    the 4-fold circular mean recovers their common orientation.
    """
    if pcd is None:
        return None, 0.0
    N = np.asarray(pcd.normals)
    vert = np.abs(N[:, 1]) < 0.2
    if vert.sum() < cfg.heading_min_wall_pts:
        return None, 0.0
    z = np.exp(4j * np.arctan2(N[vert, 2], N[vert, 0])).mean()
    return float(np.angle(z) / 4), float(abs(z) * vert.sum())


def _wrap90(a: np.ndarray) -> np.ndarray:
    return (a + np.pi / 4) % (np.pi / 2) - np.pi / 4


def plane_anchored_heading(fragments, clouds, cfg: DriftConfig) -> tuple[list[np.ndarray] | None, dict]:
    """Per-fragment yaw corrections from wall orientation (no revisits needed).

    Heading drift makes later fragments' walls rotate relative to earlier ones. Each fragment's
    dominant wall angle is compared with the capture's (weighted 4-fold circular mean); the
    deviation, median-smoothed over time and limited to ``heading_max_deg``, is removed by
    re-integrating each fragment's motion with the corrected heading (positions stay continuous).
    """
    heads = [_wall_heading(c, cfg) for c in clouds]
    ok = [h is not None for h, _ in heads]
    if sum(ok) < max(3, len(fragments) // 2):
        return None, {"reason": "too few fragments see walls"}
    h = np.array([x if x is not None else 0.0 for x, _ in heads])
    w = np.array([wt for _, wt in heads])
    g = np.angle((w * np.exp(4j * h)).sum()) / 4
    dev = np.where(ok, _wrap90(h - g), np.nan)
    dev[np.abs(dev) > np.radians(cfg.heading_max_deg)] = np.nan
    idx = np.arange(len(dev))
    good = ~np.isnan(dev)
    if good.sum() < 3:
        return None, {"reason": "deviations implausible"}
    dev = np.interp(idx, idx[good], dev[good])
    smooth = np.array([np.median(dev[max(0, k - 1):k + 2]) for k in idx])
    corrections = []
    for fr, d in zip(fragments, smooth):
        R = _yaw_matrix(d)  # heading is measured as atan2(z, x); this yaw matrix turns it by -d
        p0 = fr[0].T_world_cam[:3, 3]
        # continuity: the fragment starts where the previous fragment's correction puts its first pose
        start = p0 if not corrections else (corrections[-1] @ np.append(p0, 1.0))[:3]
        T = np.eye(4)
        T[:3, :3], T[:3, 3] = R, start - R @ p0
        corrections.append(T)
    stats = {"fragments_with_walls": int(sum(ok)), "max_deviation_deg": round(float(np.degrees(np.abs(smooth).max())), 3),
             "median_deviation_deg": round(float(np.degrees(np.median(np.abs(smooth)))), 3)}
    return corrections, stats


def _multiscale_icp(source, target, cfg: DriftConfig) -> np.ndarray:
    import open3d as o3d

    reg = o3d.pipelines.registration
    T = np.eye(4)
    for dist in cfg.icp_scales_m:
        T = reg.registration_icp(source, target, dist, T, reg.TransformationEstimationPointToPlane(),
                                 reg.ICPConvergenceCriteria(max_iteration=30)).transformation
        T = _yaw_only(T)
    return T


def _constraint_ratio(info: np.ndarray) -> float:
    """How well the overlap pins horizontal translation: min/max eigenvalue of its (x, z) block.

    Open3D orders the 6x6 information matrix as (rx, ry, rz, tx, ty, tz).
    """
    block = info[np.ix_([3, 5], [3, 5])]
    ev = np.linalg.eigvalsh(block)
    return float(ev[0] / ev[1]) if ev[1] > 0 else 0.0


def correct_drift(frameset: FrameSet, cfg: DriftConfig | None = None) -> tuple[FrameSet, DriftReport]:
    """Return a FrameSet with corrected poses (keyframes and full trajectory) and a report."""
    import open3d as o3d

    cfg = cfg or DriftConfig()
    reg = o3d.pipelines.registration
    frames = [f for f in frameset.frames if f.T_world_cam is not None]
    if len(frames) < 2 * cfg.fragment_size:
        return frameset, DriftReport(True, note="capture too short for loop closure; poses kept")

    bounds = list(range(0, len(frames), cfg.fragment_size))
    fragments = [frames[b:b + cfg.fragment_size] for b in bounds]
    if len(fragments[-1]) < cfg.fragment_size // 2 and len(fragments) > 1:  # fold a short tail in
        fragments[-2] = fragments[-2] + fragments[-1]
        fragments.pop()
    clouds = [_fragment_cloud(fr, cfg) for fr in fragments]
    centroids = np.array([np.mean([f.T_world_cam[:3, 3] for f in fr], axis=0) for fr in fragments])

    graph = reg.PoseGraph()
    for _ in fragments:
        graph.nodes.append(reg.PoseGraphNode(np.eye(4)))
    for i in range(len(fragments) - 1):  # odometry: trust ARKit between consecutive fragments
        # ARKit is reliable over the seconds between neighbouring fragments: a stiffness floor
        # keeps a fragment with little overlap from floating away during optimisation
        info = np.eye(6) * cfg.odometry_stiffness
        if clouds[i] is not None and clouds[i + 1] is not None:
            info = info + reg.get_information_matrix_from_point_clouds(
                clouds[i], clouds[i + 1], cfg.icp_max_corr_m, np.eye(4))
        graph.edges.append(reg.PoseGraphEdge(i, i + 1, np.eye(4), info, uncertain=False))

    tested = accepted = 0
    loop_edges = []
    checks: list[dict] = []
    for i in range(len(fragments)):
        for j in range(i + cfg.min_gap_fragments, len(fragments)):
            if clouds[i] is None or clouds[j] is None:
                continue
            if np.linalg.norm(centroids[i] - centroids[j]) > cfg.loop_max_dist_m:
                continue
            tested += 1
            T = _multiscale_icp(clouds[i], clouds[j], cfg)
            before = reg.evaluate_registration(clouds[i], clouds[j], cfg.eval_corr_m, np.eye(4)).fitness
            after = reg.evaluate_registration(clouds[i], clouds[j], cfg.eval_corr_m, T).fitness
            shift = float(np.linalg.norm(T[:3, 3]))
            check = {"fragments": [i, j], "fitness_before": round(before, 3), "fitness_after": round(after, 3),
                     "shift_mm": round(shift * 1000, 1), "yaw_deg": round(float(_yaw_deg(T)), 3)}
            checks.append(check)
            if after < cfg.min_tight_fitness:
                check["outcome"] = "too little overlap"
                continue
            if shift > cfg.max_loop_correction_m or abs(_yaw_deg(T)) > cfg.max_loop_correction_deg:
                check["outcome"] = "implausible correction"
                continue
            if after - before < cfg.min_fitness_gain:
                # the two passes already agree at 2 cm: evidence of low drift, nothing to correct
                check["outcome"] = "already aligned"
                continue
            info = reg.get_information_matrix_from_point_clouds(clouds[i], clouds[j], cfg.eval_corr_m, T)
            if _constraint_ratio(info) < cfg.min_constraint_ratio:
                check["outcome"] = "sliding along one wall"
                continue  # overlap is one flat wall: ICP can slide along it, so this is not evidence of drift
            check["outcome"] = "accepted"
            graph.edges.append(reg.PoseGraphEdge(i, j, T, info, uncertain=True))
            loop_edges.append(len(graph.edges) - 1)
            accepted += 1

    report = DriftReport(True, n_fragments=len(fragments), loops_tested=tested, loops_accepted=accepted,
                         checks=checks)
    if accepted == 0:
        aligned = [c for c in checks if c.get("outcome") == "already aligned"]
        why = (f"{len(aligned)} of {tested} revisits already agree at the 2 cm window" if aligned else
               f"no revisit of {tested} overlaps enough for a loop closure")
        corrections, stats = plane_anchored_heading(fragments, clouds, cfg)
        if corrections is None:
            report.note = f"{why}; plane-anchored heading not measurable ({stats['reason']}); poses kept"
            return frameset, report
        measured = (f"wall orientation deviates by up to {stats['max_deviation_deg']:.2f} deg "
                    f"(median {stats['median_deviation_deg']:.2f}) across {stats['fragments_with_walls']} fragments")
        if stats["max_deviation_deg"] < cfg.heading_apply_min_deg and not cfg.force_heading:
            report.note = (f"{why}; plane-anchored heading drift measured: {measured}, below "
                           f"{cfg.heading_apply_min_deg:.1f} deg (removable length error < 1 mm); poses kept")
            report.max_yaw_deg = stats["max_deviation_deg"]
            return frameset, report
        report.note = f"{why}; plane-anchored heading correction applied: {measured}"
        return _apply_corrections(frameset, fragments, corrections, report)

    option = reg.GlobalOptimizationOption(max_correspondence_distance=cfg.icp_max_corr_m,
                                          edge_prune_threshold=cfg.prune_threshold, reference_node=0)
    o3d.utility.set_verbosity_level(o3d.utility.VerbosityLevel.Error)
    reg.global_optimization(graph, reg.GlobalOptimizationLevenbergMarquardt(),
                            reg.GlobalOptimizationConvergenceCriteria(), option)
    surviving = {(e.source_node_id, e.target_node_id) for e in graph.edges if e.uncertain}
    report.loops_pruned = accepted - len(surviving)

    corrections = [_yaw_only(np.asarray(n.pose)) for n in graph.nodes]
    return _apply_corrections(frameset, fragments, corrections, report)


def _apply_corrections(frameset: FrameSet, fragments, corrections, report: DriftReport):
    for k, (fr, T) in enumerate(zip(fragments, corrections)):
        report.corrections.append(FragmentCorrection(
            k, frameset.frames.index(fr[0]), len(fr),
            round(float(np.linalg.norm(T[:3, 3])), 4), round(_yaw_deg(T), 3)))
    report.max_translation_m = max(c.translation_m for c in report.corrections)
    report.max_yaw_deg = max(abs(c.yaw_deg) for c in report.corrections)

    # apply: every keyframe gets its fragment's correction
    correction_of = {}
    for fr, T in zip(fragments, corrections):
        for f in fr:
            correction_of[id(f)] = T
    new_frames = [replace(f, T_world_cam=correction_of[id(f)] @ f.T_world_cam) if id(f) in correction_of else f
                  for f in frameset.frames]
    return replace(frameset, frames=new_frames,
                   trajectory=_correct_trajectory(frameset.trajectory, fragments, corrections)), report


def _correct_trajectory(traj: Trajectory | None, fragments, corrections) -> Trajectory | None:
    """Raw poses take the correction of the fragment their time falls in."""
    if traj is None:
        return None
    starts = np.array([fr[0].timestamp for fr in fragments])
    idx = np.clip(np.searchsorted(starts, traj.timestamps, side="right") - 1, 0, len(fragments) - 1)
    Rc = np.stack([T[:3, :3] for T in corrections])[idx]
    tc = np.stack([T[:3, 3] for T in corrections])[idx]
    positions = np.einsum("nij,nj->ni", Rc, traj.positions) + tc
    rotations = np.einsum("nij,njk->nik", Rc, traj.rotations)
    return Trajectory(traj.timestamps, positions, rotations)
