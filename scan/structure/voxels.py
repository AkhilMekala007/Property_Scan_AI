"""Fuse every kept frame's depth into one labelled voxel model.

Each voxel stores the mean position of the points that fell in it, an averaged
surface normal oriented toward the cameras that saw it (so it points into the
room), and label votes from the frames C4 labelled. Points from unlabelled
frames still add geometry; they pick up labels from the voxels' votes.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from scan.core.geometry import organised_points_normals
from scan.core.types import Frame
from scan.semantics.check import labels_at
from scan.semantics.classes import Surface

N_CLASSES = len(Surface)
UNKNOWN = -1
_OFF = 1 << 19  # voxel index offset; +-2^19 voxels = +-10 km at 2 cm
_M = 1 << 20

# columns of the per-point / per-voxel sum table
_COUNT, _POS, _NRM, _VOTES = 0, slice(1, 4), slice(4, 7), slice(7, 7 + N_CLASSES)
_N_COLS = 7 + N_CLASSES


@dataclass
class VoxelGrid:
    voxel_size: float
    centers: np.ndarray  # (N, 3) mean point position, world metres
    normals: np.ndarray  # (N, 3) unit, pointing toward the viewers (into free space)
    normal_consistency: np.ndarray  # (N,) |mean normal| in [0, 1]; low = edge / clutter
    counts: np.ndarray  # (N,) points accumulated
    votes: np.ndarray  # (N, N_CLASSES) confidence-weighted label votes
    labels: np.ndarray  # (N,) winning Surface, or UNKNOWN when no frame voted

    def __len__(self) -> int:
        return len(self.counts)

    def subset(self, mask: np.ndarray) -> VoxelGrid:
        return VoxelGrid(
            self.voxel_size, self.centers[mask], self.normals[mask],
            self.normal_consistency[mask], self.counts[mask], self.votes[mask], self.labels[mask],
        )

    def label_counts(self) -> dict[str, int]:
        out = {s.name.lower(): int((self.labels == s).sum()) for s in Surface}
        out["unknown"] = int((self.labels == UNKNOWN).sum())
        return out


def _keys(points: np.ndarray, voxel: float) -> np.ndarray:
    ijk = np.floor(points / voxel).astype(np.int64) + _OFF
    return (ijk[:, 0] * _M + ijk[:, 1]) * _M + ijk[:, 2]


def _reduce(keys: np.ndarray, table: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    unique, inverse = np.unique(keys, return_inverse=True)
    sums = np.empty((len(unique), table.shape[1]), np.float64)
    for j in range(table.shape[1]):
        sums[:, j] = np.bincount(inverse, weights=table[:, j], minlength=len(unique))
    return unique, sums


def _smoothed_depth(depth: np.ndarray, valid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """3x3 median to tame per-pixel LiDAR noise before normals; shrink the valid mask by one pixel."""
    filled = np.where(valid, depth, 0).astype(np.float32)
    smooth = cv2.medianBlur(filled, 3)
    eroded = cv2.erode(valid.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    return smooth, eroded


def frame_table(frame: Frame, voxel: float, stride: int) -> tuple[np.ndarray, np.ndarray] | None:
    """Voxel keys and the per-point sum table for one frame."""
    depth = frame.depth()
    if depth is None or frame.T_world_cam is None:
        return None
    conf = frame.confidence()
    valid = np.isfinite(depth) & ((conf == 2) if conf is not None else True)
    depth, valid = _smoothed_depth(depth, valid)
    P, N = organised_points_normals(depth, frame.depth_intrinsics, frame.T_world_cam, stride=stride, valid=valid)

    if frame.has_labels:
        lab = labels_at(frame, frame.depth_size)[::stride, ::stride][:-1, :-1]
        lconf = cv2.resize(frame.label_conf(), frame.depth_size, interpolation=cv2.INTER_NEAREST)
        lconf = lconf[::stride, ::stride][:-1, :-1]
    else:
        lab = lconf = None

    ok = np.all(np.isfinite(P), axis=-1) & np.all(np.isfinite(N), axis=-1)
    P, N = P[ok], N[ok]
    if len(P) == 0:
        return None
    to_cam = frame.T_world_cam[:3, 3] - P
    flip = np.einsum("ij,ij->i", N, to_cam) < 0
    N[flip] *= -1

    table = np.zeros((len(P), _N_COLS), np.float64)
    table[:, _COUNT] = 1.0
    table[:, _POS] = P
    table[:, _NRM] = N
    if lab is not None:
        rows = np.arange(len(P))
        table[rows, 7 + lab[ok].astype(int)] = lconf[ok]
    return _keys(P, voxel), table


def fuse(frames: list[Frame], voxel: float = 0.02, stride: int = 2, min_count: int = 3,
         chunk: int = 40) -> VoxelGrid:
    """Accumulate all posed depth frames into a voxel grid, chunk by chunk to bound memory."""
    partial_keys, partial_sums = [], []
    pending_k, pending_t = [], []

    def flush():
        if pending_k:
            k, s = _reduce(np.concatenate(pending_k), np.concatenate(pending_t))
            partial_keys.append(k)
            partial_sums.append(s)
            pending_k.clear()
            pending_t.clear()

    for i, frame in enumerate(frames):
        result = frame_table(frame, voxel, stride)
        if result is not None:
            pending_k.append(result[0])
            pending_t.append(result[1])
        if (i + 1) % chunk == 0:
            flush()
    flush()
    if not partial_keys:
        raise ValueError("no posed depth frames to fuse")

    _, sums = _reduce(np.concatenate(partial_keys), np.concatenate(partial_sums))
    counts = sums[:, _COUNT]
    keep = counts >= min_count
    sums, counts = sums[keep], counts[keep]

    centers = sums[:, _POS] / counts[:, None]
    nsum = sums[:, _NRM]
    nlen = np.linalg.norm(nsum, axis=1)
    normals = nsum / np.maximum(nlen, 1e-9)[:, None]
    votes = sums[:, _VOTES].astype(np.float32)
    labels = np.where(votes.sum(axis=1) > 0, votes.argmax(axis=1), UNKNOWN).astype(np.int8)
    return VoxelGrid(
        voxel_size=voxel,
        centers=centers,
        normals=normals,
        normal_consistency=nlen / counts,
        counts=counts.astype(np.int32),
        votes=votes,
        labels=labels,
    )
