import json

import yaml

from scan.bench import benchmark, to_markdown


def _m(v, lo, hi):
    return {"value": v, "lo": lo, "hi": hi}


def test_benchmark_rows_gates_and_head_to_head(tmp_path):
    result = {
        "capture": {"tier": "lidar"},
        "rooms": [{"walls": [{"id": "room_1.wall_0", "length": _m(3.170, 3.150, 3.190)},
                             {"id": "room_1.wall_1", "length": _m(3.300, 3.290, 3.310)}],
                   "ceiling_id": "room_1.ceiling", "ceiling_height": _m(2.905, 2.89, 2.92)}],
        "openings": [{"id": 0, "width": _m(0.80, 0.78, 0.82)}],
    }
    gt = {
        "rooms": {"bedroom3": {"walls": {"A": 3.165, "B": 3.270}, "ceiling": 2.900,
                               "doors": [{"id": "main", "width": 0.81}]}},
        "mapping": {"lidar_flat": {"bedroom3.A": "room_1.wall_0", "bedroom3.B": "room_1.wall_1",
                                   "bedroom3.ceiling": "room_1.ceiling", "bedroom3.door_main": "opening_0"}},
        "competitor": {"rooms": {"bedroom3": {"walls": {"A": 3.20, "B": 3.27}, "ceiling": 2.95}}},
    }
    rp, gp = tmp_path / "result.json", tmp_path / "gt.yaml"
    rp.write_text(json.dumps(result))
    gp.write_text(yaml.safe_dump(gt))
    rep = benchmark(rp, gp, "lidar_flat")
    rows = {r.key: r for r in rep.rows}
    assert rows["bedroom3.A"].passed and rows["bedroom3.A"].inside
    assert not rows["bedroom3.B"].passed and not rows["bedroom3.B"].inside  # 3 cm off
    assert rows["bedroom3.A"].verdict == "win"  # 0.5 cm vs app 3.5 cm
    assert rows["bedroom3.B"].verdict == "loss"  # 3 cm vs app 0
    assert rows["bedroom3.ceiling"].verdict == "win"
    assert rows["bedroom3.door_main"].passed  # 1 cm <= 2 cm
    s = rep.summary
    assert s["wall"] == {"passed": 1, "total": 2, "rate": 0.5}
    assert s["head_to_head"]["beat_or_tie"] == 2 and s["head_to_head"]["total"] == 3
    assert "bedroom3.A" in to_markdown(rep)


def test_unmapped_item_counts_as_miss(tmp_path):
    result = {"capture": {"tier": "video"}, "rooms": [], "openings": []}
    gt = {"rooms": {"hall": {"walls": {"A": 4.0}}}, "mapping": {}}
    rp, gp = tmp_path / "r.json", tmp_path / "g.yaml"
    rp.write_text(json.dumps(result))
    gp.write_text(yaml.safe_dump(gt))
    rep = benchmark(rp, gp, "video_flat")
    assert rep.summary["missed"] == 1 and not rep.rows[0].passed
