from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from scan.damage import DamageConfig, DamageRegion, fuse
from scan.damage.detect import DetectConfig, _box_to_sensor, filter_detections
from scan.damage.measure import MeasureConfig, SurfaceRef, ViewMeasurement, footprint, project_mask, refine_mask
from scan.measure import RoomMeasurement, WallMeasure
from scan.rooms.floormap import GridFrame
from scan.rules import apply_rules, load_rules, safe_eval
from scan.semantics.classes import Surface
from scan.semantics.upright import to_upright
from tests.test_qc import LOOK_DOWN, flat_frame

MCFG = MeasureConfig()


def wall_image(h=360, w=480, stain=None, colour=(150, 110, 70)):
    img = np.full((h, w, 3), (225, 220, 205), np.uint8)
    img = cv2.add(img, np.random.default_rng(0).integers(0, 4, img.shape, dtype=np.uint8))
    if stain is not None:
        cv2.circle(img, stain[:2], stain[2], colour, -1)
    return img


# ---------- detection filtering ----------

def test_threshold_decoy_and_size_prior():
    raw = [("water_stain", "p", 0.25, (100, 100, 140, 140)),  # kept
           ("water_stain", "p", 0.10, (300, 300, 320, 320)),  # below threshold
           ("crack", "p", 0.30, (10, 10, 400, 300)),  # covers most of the image: scene structure
           ("water_stain", "p", 0.20, (200, 50, 240, 90)), ("decoy", "a poster", 0.5, (190, 40, 260, 110))]
    kept = filter_detections(raw, DetectConfig(), image_area=480 * 360)
    assert [k[2] for k in kept] == [0.25]


@pytest.mark.parametrize("k", [0, 1, 2, 3])
def test_box_maps_back_to_sensor_orientation(k):
    img = np.zeros((48, 64), np.uint8)
    img[10:14, 40:46] = 255  # marker in sensor orientation
    up = to_upright(img, k)
    ys, xs = np.nonzero(up)
    box = _box_to_sensor((xs.min(), ys.min(), xs.max(), ys.max()), k, up.shape[1], up.shape[0])
    assert box == pytest.approx((40, 10, 45, 13))


# ---------- mask refinement ----------

def test_stain_mask_follows_the_stain():
    labels = np.full((360, 480), Surface.WALL, np.uint8)
    mask = refine_mask(wall_image(stain=(240, 180, 30)), labels, (200, 140, 280, 220), MCFG)
    assert isinstance(mask, np.ndarray)
    assert mask.sum() == pytest.approx(np.pi * 30 ** 2, rel=0.15)


def test_clean_wall_gives_no_mask():
    labels = np.full((360, 480), Surface.WALL, np.uint8)
    assert refine_mask(wall_image(), labels, (200, 140, 280, 220), MCFG) is None


def test_mark_along_a_surface_boundary_is_an_edge():
    img = wall_image()
    labels = np.full((360, 480), Surface.WALL, np.uint8)
    labels[:180] = Surface.CEILING
    cv2.line(img, (150, 180), (330, 180), (90, 90, 90), 4)  # the corner line
    assert refine_mask(img, labels, (140, 150, 340, 210), MCFG) == "edge"


def test_stain_off_surface_pixels_is_ignored():
    labels = np.full((360, 480), Surface.OTHER, np.uint8)  # e.g. on a sofa
    assert refine_mask(wall_image(stain=(240, 180, 30)), labels, (200, 140, 280, 220), MCFG) is None


# ---------- measurement on a surface ----------

def test_footprint_area():
    xs, ys = np.meshgrid(np.arange(0, 0.2, 0.004), np.arange(0, 0.3, 0.004))
    area, sigma, centroid, extent, length, outline, cells, bottom = footprint(np.column_stack([xs.ravel(), ys.ravel()]))
    assert area == pytest.approx(0.06, rel=0.08)
    assert extent == pytest.approx((0.2, 0.3), abs=0.015)
    assert 0 < sigma < 0.02 and bottom == pytest.approx(0.0, abs=0.01)


def test_floor_stain_projects_onto_the_floor_with_metric_area():
    frame = flat_frame(0, LOOK_DOWN, 1.4)  # camera 1.4 m above a floor, 64x48 depth, fx 40
    corners = [(-3.0, -3.0), (3.0, -3.0), (3.0, 3.0), (-3.0, 3.0)]
    walls = [WallMeasure(k, 6.0, 0.001, [k], 1.0, False) for k in range(4)]
    room = RoomMeasurement(1, "room_1", "room", corners, walls, 36.0, 0.1, 24.0, None, None, "n/a", 0.0,
                           corners_uv=corners)
    model = SimpleNamespace(floor=SimpleNamespace(a=0.0, b=0.0, c=-1.4))
    mask = np.zeros((48, 64), bool)
    mask[14:34, 22:42] = True  # 20 x 20 px at 1.4 m / fx 40 -> 0.7 m x 0.7 m
    hit = project_mask(frame, mask, [room], model, GridFrame(0.0, (-5.0, -5.0), 0.05, (200, 200)),
                       MeasureConfig(pixel_stride=1))
    ref, coords, spacing = hit
    assert ref.id == "room_1.floor" and ref.kind == "floor"
    assert spacing == pytest.approx(1.4 / 40, rel=0.01)
    area = footprint(coords, spacing)[0]
    assert area == pytest.approx(0.49, rel=0.12)


# ---------- fusion ----------

def view(frame, score, area, centroid=(1.0, 1.2), cls="water_stain", surface="room_1.wall_2"):
    ref = SurfaceRef(surface, 1, "room_1", "wall", 2)
    return ViewMeasurement(frame, cls, score, ref, area, 0.01, centroid, (0.3, 0.3), 0.42, 0.9, [], set())


def test_views_of_one_stain_fuse():
    regions = fuse([view(1, 0.25, 0.10), view(2, 0.22, 0.12), view(3, 0.20, 0.11, centroid=(1.1, 1.25))],
                   DamageConfig(), {})
    assert len(regions) == 1
    r = regions[0]
    assert r.n_views == 3 and r.area_m2 == pytest.approx(0.11) and r.surface == "room_1.wall_2"


def test_single_weak_view_is_dropped_but_strong_one_kept():
    dropped = {}
    assert fuse([view(1, 0.25, 0.1)], DamageConfig(), dropped) == [] and dropped["single_weak_view"] == 1
    assert len(fuse([view(1, 0.6, 0.1)], DamageConfig(), {})) == 1


def test_class_vote_across_views():
    regions = fuse([view(1, 0.3, 0.1, cls="crack"), view(2, 0.25, 0.1), view(3, 0.25, 0.1)], DamageConfig(), {})
    assert regions[0].cls == "water_stain"


# ---------- rules ----------

def region(cls, kind, area=0.2, sigma=0.02, length=0.6, bottom=None, wall_index=None):
    surface = f"room_1.{kind}" if kind != "wall" else f"room_1.wall_{wall_index}"
    return DamageRegion(0, 1, "room_1", surface, kind, wall_index, cls, area, sigma, length, (0.4, 0.5), bottom,
                        (1.0, 1.0), 0.6, 3, [])


def room_with_ceiling():
    corners = [(0, 0), (4, 0), (4, 3), (0, 3)]
    walls = [WallMeasure(k, [4, 3, 4, 3][k], 0.001, [k], 1.0, False) for k in range(4)]
    return RoomMeasurement(1, "room_1", "room", corners, walls, 12.0, 0.05, 14.0, 2.5, 0.001, None, 0.0,
                           corners_uv=corners)


def test_ceiling_water_stain_flags_leak_with_scope():
    flags, scope = apply_rules([region("water_stain", "ceiling")], [room_with_ceiling()])
    assert [f.rule_id for f in flags] == ["R01_ceiling_water_stain"]
    items = {s.item: s for s in scope}
    assert items["Stain-blocking primer and repaint full ceiling"].qty == pytest.approx(12.0)
    patch = items["Cut out and replace water-damaged ceiling board"]
    assert patch.qty == pytest.approx(0.5)  # min 0.5 m2 for a 0.2 m2 stain
    assert all(s.surface == "room_1.ceiling" for s in scope)


def test_wall_stain_low_vs_high():
    low = apply_rules([region("water_stain", "wall", bottom=0.1, wall_index=0)], [room_with_ceiling()])[0]
    high = apply_rules([region("water_stain", "wall", bottom=1.2, wall_index=0)], [room_with_ceiling()])[0]
    assert [f.rule_id for f in low] == ["R02_wall_water_stain_low"]
    assert [f.rule_id for f in high] == ["R03_wall_water_stain_high"]


def test_crack_length_decides_severity():
    long = apply_rules([region("crack", "wall", length=1.4, wall_index=1)], [room_with_ceiling()])[0]
    short = apply_rules([region("crack", "wall", length=0.4, wall_index=1)], [room_with_ceiling()])[0]
    assert long[0].rule_id == "R05_long_or_ceiling_crack" and long[0].severity == "high"
    assert short[0].rule_id == "R07_short_wall_crack" and short[0].severity == "low"


def test_quantity_sigma_propagates_from_area():
    _, scope = apply_rules([region("mold", "wall", area=1.0, sigma=0.1, wall_index=0)], [room_with_ceiling()])
    remediation = [s for s in scope if s.item.startswith("Mold remediation")][0]
    assert remediation.qty == pytest.approx(2.0) and remediation.qty_sigma == pytest.approx(0.2)


def test_every_rule_file_entry_is_well_formed():
    rules = load_rules().rules
    assert len(rules) >= 8
    variables = {"area": 1, "length": 1, "surface_area": 1, "wall_length": 1, "ceiling_height": 1, "room_area": 1}
    for r in rules:
        assert r["flag"]["reason"] and r["flag"]["severity"] in {"low", "medium", "high"}
        for s in r["scope"]:
            assert safe_eval(s["qty"], variables) >= 0


def test_formulas_cannot_run_code():
    with pytest.raises(ValueError):
        safe_eval("__import__('os').system('echo hi')", {})
    with pytest.raises(ValueError):
        safe_eval("area.__class__", {"area": 1.0})
