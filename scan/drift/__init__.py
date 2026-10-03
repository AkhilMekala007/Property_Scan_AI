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
            if (after < cfg.min_tight_fitness or after - before < cfg.min_fitness_gain
                    or np.linalg.norm(T[:3, 3]) > cfg.max_loop_correction_m
                    or abs(_yaw_deg(T)) > cfg.max_loop_correction_deg):
                continue
            info = reg.get_information_matrix_from_point_clouds(clouds[i], clouds[j], cfg.eval_corr_m, T)
            if _constraint_ratio(info) < cfg.min_constraint_ratio:
                continue  # overlap is one flat wall: ICP can slide along it, so this is not evidence of drift
            graph.edges.append(reg.PoseGraphEdge(i, j, T, info, uncertain=True))
            loop_edges.append(len(graph.edges) - 1)
            accepted += 1

    report = DriftReport(True, n_fragments=len(fragments), loops_tested=tested, loops_accepted=accepted)
    if accepted == 0:
        report.note = "no loop closures found; poses kept"
        return frameset, report

    option = reg.GlobalOptimizationOption(max_correspondence_distance=cfg.icp_max_corr_m,
                                          edge_prune_threshold=cfg.prune_threshold, reference_node=0)
    o3d.utility.set_verbosity_level(o3d.utility.VerbosityLevel.Error)
    reg.global_optimization(graph, reg.GlobalOptimizationLevenbergMarquardt(),
                            reg.GlobalOptimizationConvergenceCriteria(), option)
    surviving = {(e.source_node_id, e.target_node_id) for e in graph.edges if e.uncertain}
    report.loops_pruned = accepted - len(surviving)

    corrections = [_yaw_only(np.asarray(n.pose)) for n in graph.nodes]
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
