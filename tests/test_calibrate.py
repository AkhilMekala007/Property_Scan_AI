import json

from scan.calibrate import apply, fit, load_factors, write_factors
from scan.contract.budget import Z90


def _row(err, sigma, kind="wall", truth=3.0):
    v = truth + err
    return {"kind": kind, "ours": v, "truth": truth, "lo": v - Z90 * sigma, "hi": v + Z90 * sigma, "passed": True}


def test_factor_widens_to_cover_and_never_narrows(tmp_path):
    rows = [_row(e, 0.01) for e in (0.001, -0.004, 0.006, 0.01, -0.03)]  # one error at 3 sigma
    rows += [_row(e, 0.05, kind="ceiling") for e in (0.001, 0.002, -0.001, 0.003)]  # all well inside
    rp = tmp_path / "rep.json"
    rp.write_text(json.dumps({"tier": "lidar", "rows": rows}))
    factors = {(f.tier, f.kind): f for f in fit([rp])}
    w = factors[("lidar", "wall")]
    assert w.k > 1.8 and w.coverage_after == 1.0 and w.coverage_before == 0.8
    assert factors[("lidar", "ceiling")].k == 1.0  # never narrower than the budget

    path = write_factors(list(factors.values()), tmp_path / "f.json", [rp])
    result = {"capture": {"tier": "lidar"}, "processing": {"calibrated": False, "interval_method": ""},
              "rooms": [{"walls": [{"length": {"value": 3.0, "lo": 2.98, "hi": 3.02, "sigma": 0.0122,
                                               "bound": "two_sided", "calibrated": False}}],
                         "ceiling_height": None}], "openings": []}
    out = apply(result, load_factors(path))
    m = out["rooms"][0]["walls"][0]["length"]
    assert m["value"] == 3.0 and m["calibrated"] and m["hi"] - m["lo"] > 0.04 * 1.8
    assert out["processing"]["calibrated"]
