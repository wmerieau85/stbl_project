import numpy as np

from scripts.draft.engine import roto_points

CATS = ["pts", "tov"]


def test_roto_points_order_and_lower_is_better():
    values = np.array([[100.0, 10.0], [90.0, 20.0], [80.0, 30.0]])
    points = roto_points(values, CATS, [1.0, 1.0])
    assert points[0, 0] > points[1, 0] > points[2, 0]      # plus de points = mieux
    assert points[0, 1] > points[1, 1] > points[2, 1]      # moins de pertes de balle = mieux
    assert abs(points[:, 0].sum() - 6.0) < 1e-9      # 1 + 2 + 3 points


def test_roto_points_weights():
    values = np.array([[100.0, 10.0], [90.0, 20.0]])
    assert np.allclose(roto_points(values, CATS, [2.0, 0.0])[:, 1], 0.0)
