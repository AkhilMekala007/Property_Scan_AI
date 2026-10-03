import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from scan.contract import Result, build_result, json_schema, measure, to_json
from scan.contract.budget import Z90
from scan.core.types import CaptureMeta, Tier
from scan.damage import DamageRegion
from scan.drift import DriftReport
from scan.qc.report import Coverage, Issue, QualityReport
from scan.rules import apply_rules
from scan.stitch import stitch
from tests.conftest import REPO_ROOT
from tests.test_stitch import layout, opening, room


def test_measure_two_sided_interval():
    m = measure(3.0, 0.003, 0.004, "m")
    assert m.sigma == pytest.approx(0.005)
    assert m.lo == pytest.approx(3.0 - Z90 * 0.005, abs=1e-4) and m.hi == pytest.approx(3.0 + Z90 * 0.005, abs=1e-4)
    assert m.confidence == 0.9 and m.bound == "two_sided" and m.calibrated is False


def test_measure_lower_bound_has_no_upper_end():
    m = measure(1.7, 0.0, 0.02, "m", lower_bound=True)
    assert m.bound == "lower" and m.hi is None and m.lo == 1.7


def test_non_negative_quantities_never_go_below_zero():
    assert measure(0.01, 0.05, 0.0, "m2").lo == 0.0


def fake_pipeline_result():
    rooms = [room(1, 0, 0, 5.0, 3), room(2, 5.1, 0, 8, 3)]
    ops = [opening(0, 1, 1, 0.80, connects=2), opening(1, 2, 3, 0.80, connects=1)]
    plan = stitch(rooms, ops, layout())
    dmg = DamageRegion(0, 1, "room_1", "room_1.ceiling", "ceiling", None, "water_stain", 0.25, 0.02, 0.7,
                       (0.5, 0.5), None, (1.0, 1.0), 0.7, 3, [(0.8, 0.8), (1.2, 0.8), (1.2, 1.2)])
    flags, scope = apply_rules([dmg], plan.rooms)
    meta = CaptureMeta("synthetic", Tier.LIDAR, "stray_scanner", source_dir=None, device_model="iPhone 15 Pro",
                       n_frames_raw=100, duration_s=30.0)
    qc = QualityReport("synthetic", "lidar", 50, 48, 0.93, 140.0, Coverage(),
                       [Issue("CEILING_NOT_SEEN", "warning", "ceiling not seen", "tilt up")], [])
    return SimpleNamespace(
        frameset=SimpleNamespace(meta=meta), qc=SimpleNamespace(report=qc),
        labelled=SimpleNamespace(frames=[None] * 48), drift=DriftReport(True, loops_accepted=1, loops_tested=5),
        plan=plan, damage=SimpleNamespace(regions=[dmg]), flags=flags, scope=scope)


def test_build_result_fills_the_contract():
    result = build_result(fake_pipeline_result(), runtime_s=12.3)
    assert result.capture.device_supported is True
    assert result.property.rooms_count == 2 and result.property.connected
    assert len(result.property.shared_walls) == 1
    assert result.property.shared_walls[0].thickness.value == pytest.approx(0.10)
    assert len(result.openings) == 1 and result.openings[0].views == 2
    assert result.openings[0].wall_ids == ["room_1.wall_1", "room_2.wall_3"]
    r1 = result.rooms[0]
    assert r1.walls[0].id == "room_1.wall_0" and r1.ceiling_id == "room_1.ceiling"
    assert r1.walls[0].length.lo < 5.0 < r1.walls[0].length.hi
    assert result.damage[0].surface_id == "room_1.ceiling"
    assert result.flags[0].rule_id == "R01_ceiling_water_stain"
    assert {s.surface_id for s in result.scope} == {"room_1.ceiling"}
    assert result.processing.calibrated is False and result.processing.offline


def test_json_round_trip_uses_class_key():
    text = to_json(build_result(fake_pipeline_result(), runtime_s=1.0))
    data = json.loads(text)
    assert data["damage"][0]["class"] == "water_stain"
    assert Result.model_validate_json(text).damage[0].cls == "water_stain"


def test_contract_rejects_bad_data():
    data = json.loads(to_json(build_result(fake_pipeline_result(), runtime_s=1.0)))
    data["damage"][0]["class"] = "graffiti"
    with pytest.raises(ValidationError):
        Result.model_validate(data)
    data = json.loads(to_json(build_result(fake_pipeline_result(), runtime_s=1.0)))
    data["rooms"][0]["unexpected"] = 1
    with pytest.raises(ValidationError):
        Result.model_validate(data)


def test_published_schema_is_in_sync_with_the_models():
    published = json.loads((REPO_ROOT / "schema" / "result.schema.json").read_text(encoding="utf-8"))
    assert published == json_schema(), "run `scan schema` to regenerate schema/result.schema.json"
