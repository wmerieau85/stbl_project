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



def _need_simulator():
    from types import SimpleNamespace

    from scripts.draft.engine import Simulator
    from scripts.draft.pool import STATS

    def player(idx, pts, reb):
        per_game = np.zeros(len(STATS))
        per_game[STATS.index("pts")] = pts
        per_game[STATS.index("reb")] = reb
        return SimpleNamespace(idx=idx, gp=82, per_game=per_game, value_avg=1, value_tot=1, groups=set(), market=idx)

    # 0 : marqueur, 1 : rebondeur ; A domine les rebonds mais est au coude à coude avec C aux points
    players = [player(0, 30, 0), player(1, 0, 30), player(2, 40, 50), player(3, 50, 45), player(4, 42, 30)]
    sim = object.__new__(Simulator)
    sim.pool = type("Pool", (), {"players": players, "__len__": lambda self: len(players)})()
    sim.state = SimpleNamespace(order=["A", "B", "C"], my_team="C", teams=3)
    sim.team_index = {t: i for i, t in enumerate(sim.state.order)}
    sim.categories = ("reb", "pts")
    sim.weights = np.ones(2)
    sim._init_need()
    rosters = {"A": [players[2]], "B": [players[3]], "C": [players[4]]}
    return sim, players, rosters


def test_need_gains_follow_close_categories():
    sim, _, rosters = _need_simulator()
    gain = sim._need_state(rosters)["gain"]
    assert gain[0, 0] > gain[1, 0]      # A : les points rapportent plus que les rebonds


def test_need_weight_grows_with_rounds():
    sim, _, _ = _need_simulator()
    sim.rounds, sim.need_weight = 12, 2.0
    assert sim._need_weight(0) == 0.0
    assert 0 < sim._need_weight(3 * 5) < sim._need_weight(3 * 11) == 2.0


def test_prune_keeps_finalists_and_drops_clear_losers():
    from types import SimpleNamespace
    from scripts.draft.engine import Candidate, Simulator

    def cand(mean, spread, runs=20, avail=20):
        c = Candidate(player=None, sims=20, available_now=avail, runs=runs)
        c.points = mean * runs
        c.points_sq = (mean * mean + spread * spread) * runs
        return c

    sim = SimpleNamespace(keep=2, prune_z=2.0)
    cands = {0: cand(80, 1), 1: cand(79.8, 1), 2: cand(70, 1), 3: cand(79.9, 1), 4: cand(90, 1, avail=2)}
    assert Simulator._prune(sim, cands, 20) == 3       # 2 écarté (loin), 4 écarté (dispo 10 %)
    assert not cands[2].active and not cands[4].active and cands[1].active
    sim.keep = 3
    cands = {i: cand(80 - 5 * i, 1) for i in range(5)}
    assert Simulator._prune(sim, cands, 20) == 3       # jamais sous finalists
