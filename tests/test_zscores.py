import pytest

from scripts.weighting.zscores import _phase_min_gp, add_zscores


def _league(min_gp):
    return {"teams": 2, "roster": {"G": 2, "F": 1}, "format": "roto", "games": {"per_slot": 82},
            "zscore": {"min_gp": min_gp, "iterations": 3},
            "categories": {c: 1 for c in ("fgp", "fg3m", "ftp", "reb", "ast", "stl", "blk", "tov", "pts")}}


def _players(max_gp):
    out = []
    for i in range(12):
        gp = 1 + i % max_gp
        out.append({"gp": gp, "fgm": (4 + i % 3) * gp, "fga": 10 * gp, "ftm": (2 + i % 2) * gp, "fta": 3 * gp,
                    "fg3m": (i % 4) * gp, "reb": (3 + i) * gp, "ast": (1 + i % 5) * gp, "stl": (i % 3) * gp,
                    "blk": (i % 2) * gp, "tov": (1 + i % 3) * gp, "pts": (8 + 2 * i) * gp})
    return out


def test_min_gp_is_scaled_to_the_games_of_the_phase():
    """Phase season en début de saison (3 matchs joués) : un seuil de 20 matchs prévu pour une saison
    complète ne doit pas vider le groupe de référence (z-scores vides, somme à 0)."""
    players = _players(3)
    add_zscores(players, _league(20))
    assert all(p["z_pts_avg"] is not None and p["z_pts_tot"] is not None for p in players)
    assert any(p["z_sum_avg"] != 0 for p in players)

    same = _players(3)
    add_zscores(same, _league(0))
    assert [p["rank_avg"] for p in players] == [p["rank_avg"] for p in same]


@pytest.mark.parametrize("max_gp,expected", [(82, 20), (90, 20), (41, 10), (3, 20 * 3 / 82), (0, 20 / 82)])
def test_phase_min_gp(max_gp, expected):
    players = [{"gp": max_gp}, {"gp": 1}]
    assert _phase_min_gp(players, _league(20)) == pytest.approx(expected)
