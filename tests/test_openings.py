import numpy as np
import pytest

from scan.openings import OpeningConfig, _classify_and_measure, _components
from scan.openings.elevation import Elevation, WallSpec

RES = 0.02
CFG = OpeningConfig()
SPEC = WallSpec("u", 0.0, +1, 0.0, 4.0, 2.6)  # a 4 m wall, 2.6 m high


def make_wall(through=(), door=(), window=(), gaps=(), n_rays=40000, wall_top=2.6, seed=0):
    """Elevation of a wall with openings given as (s0, h0, s1, h1) boxes in metres.

    through: seen-through openings (rays cross there); door/window: label boxes;
    gaps: unobserved areas (no evidence at all, e.g. furniture in front of the wall).
    """
    rng = np.random.default_rng(seed)
    rows, cols = int(SPEC.height / RES) + 1, int((SPEC.s1 - SPEC.s0) / RES) + 1
    s_c = (np.arange(cols) + 0.5) * RES
    h_c = (np.arange(rows) + 0.5) * RES
    solid = np.zeros((rows, cols), bool)
    solid[h_c[:, None].repeat(cols, 1) < wall_top] = True
    through_n = np.zeros((rows, cols), np.int32)
    door_l = np.zeros((rows, cols), np.int32)
    window_l = np.zeros((rows, cols), np.int32)

    def box_mask(b):
        s0, h0, s1, h1 = b
        return (h_c[:, None] >= h0) & (h_c[:, None] < h1) & (s_c[None, :] >= s0) & (s_c[None, :] < s1)

    cross_s, cross_h = [], []
    for b in through:
        m = box_mask(b)
        solid[m] = False
        cs, ch = rng.uniform(b[0], b[2], n_rays), rng.uniform(b[1], b[3], n_rays)
        np.add.at(through_n, ((ch / RES).astype(int), (cs / RES).astype(int)), 1)
        cross_s.append(cs)
        cross_h.append(ch)
    for b in door:
        m = box_mask(b)
        solid[m] = False
        door_l[m] = 3
    for b in window:
        m = box_mask(b)
        solid[m] = False
        window_l[m] = 3
    for b in gaps:
        solid[box_mask(b)] = False
    rr, cc = np.nonzero(solid)
    return Elevation(
        SPEC, RES, solid, through_n, door_l, window_l,
        solid_s=(cc + 0.5) * RES, solid_h=(rr + 0.5) * RES,
        cross_s=np.concatenate(cross_s) if cross_s else np.zeros(0),
        cross_h=np.concatenate(cross_h) if cross_h else np.zeros(0),
    )


def detect(el):
    out = []
    for c0, r0, c1, r1, mask in _components(el, CFG):
        r = _classify_and_measure(el, c0, r0, c1, r1, mask, CFG)
        if r is not None:
            kind, left, right, sigma, n_jambs, head, sill, covered, head_seen, evidence = r
            out.append(dict(kind=kind, width=right - left, left=left, sigma=sigma, jambs=n_jambs, head=head,
                            sill=sill, covered=covered, head_seen=head_seen, low=evidence["low_evidence"]))
    return out


def test_open_door_width_and_head():
    el = make_wall(through=[(1.0, 0.0, 1.8, 2.1)], door=[(0.96, 0.0, 1.0, 2.1), (1.8, 0.0, 1.84, 2.1)])
    (d,) = detect(el)
    assert d["kind"] == "door"
    assert d["width"] == pytest.approx(0.80, abs=0.02)
    assert d["left"] == pytest.approx(1.00, abs=0.02)
    assert d["head"] == pytest.approx(2.10, abs=0.03) and d["head_seen"]
    assert d["jambs"] == 2 and not d["covered"] and not d["low"]


def test_window_with_sill():
    (w,) = detect(make_wall(through=[(2.5, 0.9, 3.5, 2.0)]))
    assert w["kind"] == "window"
    assert w["width"] == pytest.approx(1.00, abs=0.02)
    assert w["sill"] == pytest.approx(0.90, abs=0.03)


def test_window_split_by_a_transom_is_one_window():
    el = make_wall(through=[(2.5, 0.9, 3.5, 1.4), (2.5, 1.46, 3.5, 2.0)])
    (w,) = detect(el)
    assert w["kind"] == "window"
    assert w["sill"] == pytest.approx(0.90, abs=0.03)


def test_wide_see_through_gap_is_an_open_passage():
    el = make_wall(through=[(0.5, 0.0, 2.9, 2.3)], door=[(0.46, 0.0, 0.5, 2.3)])
    (o,) = detect(el)
    assert o["kind"] == "opening"
    assert o["width"] == pytest.approx(2.4, abs=0.03)


def test_closed_door_from_labels_only():
    (d,) = detect(make_wall(door=[(1.0, 0.0, 1.85, 2.05)]))
    assert d["kind"] == "door" and d["covered"]


def test_wardrobe_door_labels_are_rejected():
    assert detect(make_wall(door=[(1.0, 0.0, 1.4, 1.0)])) == []  # too small and short for a door


def test_furniture_hiding_the_wall_is_not_an_opening():
    assert detect(make_wall(gaps=[(1.0, 0.0, 2.5, 0.9)])) == []


def test_door_head_unseen_when_wall_top_not_observed():
    el = make_wall(through=[(1.0, 0.0, 1.8, 1.5)], door=[(0.96, 0.0, 1.0, 1.5)], wall_top=1.5)
    (d,) = detect(el)
    assert d["kind"] == "door"
    assert not d["head_seen"]


def test_few_rays_flag_low_evidence_and_widen_sigma():
    el = make_wall(through=[(1.0, 0.0, 1.8, 2.1)], door=[(0.96, 0.0, 1.0, 2.1)], n_rays=1500)
    (d,) = detect(el)
    assert d["low"] and d["sigma"] >= 0.05
