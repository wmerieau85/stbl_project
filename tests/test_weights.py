import pytest

from scripts.weighting.weights import FINAL_PHASES, WeightsError, load_grid, parse_weight


@pytest.mark.parametrize("text,value", [("22,50%", 0.225), ("22.5%", 0.225), ("0.225", 0.225), ("22.5", 0.225),
                                        ("", 0.0)])
def test_parse_weight(text, value):
    assert parse_weight(text) == pytest.approx(value)


@pytest.mark.parametrize("phase", FINAL_PHASES)
def test_grids_sum_to_one(phase):
    grid = load_grid(phase, "2026-27")
    assert grid["slots"]
    assert sum(s["weights"]["gp"] for s in grid["slots"]) == pytest.approx(1.0)


def test_invalid_grid_rejected(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text(";lt01;lt02\nsite;fs.ros;fp.ros\nGP;50%;60%\nMIN;50%;50%\nSTATS;50%;50%\n", encoding="utf-8")
    with pytest.raises((WeightsError, ValueError)):
        load_grid("lt", "2026-27", path=str(path))
