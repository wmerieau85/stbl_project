"""Recommandation de bout en bout sur une petite ligue fictive (4 équipes, 4 tours)."""
import numpy as np

from scripts.draft.engine import Simulator
from scripts.draft.pool import STATS, Player, Pool
from scripts.draft.state import build_state

CATS = ("fgp", "fg3m", "ftp", "reb", "ast", "stl", "blk", "tov", "pts")


def _league(model="adp"):
    return {
        "teams": 4, "format": "roto", "categories": {c: 1 for c in CATS},
        "roster": {"G": 1, "F": 1, "C": 1, "UTIL": 1}, "games": {"per_slot": 82},
        "draft": {"order": ["A", "B", "C", "D"], "rounds": 4, "my_team": "B", "type": "snake",
                  "simulations": 30, "simulations_min": 10, "candidates": 12, "adp_noise": 0.15,
                  "opponent_model": model, "time_budget": 0},
    }


def _pool(monkeypatch):
    monkeypatch.setattr("scripts.draft.pool.load_aliases", lambda: {})
    rng = np.random.default_rng(7)
    players = []
    positions = ["PG", "SF", "C"]
    for i in range(30):
        scale = 1.0 - i / 40                      # du meilleur au moins bon
        pg = np.zeros(len(STATS))
        for stat, base in (("pts", 20), ("reb", 7), ("ast", 5), ("stl", 1.2), ("blk", 0.8), ("tov", 2),
                           ("fg3m", 2), ("fga", 15), ("fta", 4)):
            pg[STATS.index(stat)] = base * scale * rng.uniform(0.8, 1.2)
        pg[STATS.index("fgm")] = pg[STATS.index("fga")] * 0.47
        pg[STATS.index("ftm")] = pg[STATS.index("fta")] * 0.78
        pos = positions[i % 3]
        group = {"PG": "G", "SF": "F", "C": "C"}[pos]
        players.append(Player(idx=i, player_id=i, name=f"Joueur {i}", team="X", positions=pos, gp=75,
                              per_game=pg, value_avg=10 * scale, value_tot=10 * scale, rank_tot=i + 1,
                              adp=i + 1.0, market=i + 1.0, groups={group}))
    star = players[5]
    star.per_game = star.per_game * 3                 # nettement au-dessus de tout le monde, ADP 6
    return Pool(players)


def test_recommendation_end_to_end(monkeypatch):
    pool = _pool(monkeypatch)
    league = _league()
    # choix 1 : A prend le n°1, puis c'est à B (moi)
    state = build_state(league, pool, [(1, 1, "Joueur 0")])
    reco = Simulator(league, pool, state, seed=1).recommend()
    assert reco.my_pick == 1 and reco.my_next == 6           # snake : 2e choix au tour 2, rang 3 en partant de la fin
    assert reco.candidates and all(c.player.name != "Joueur 0" for c in reco.candidates)
    assert reco.candidates[0].player.name == "Joueur 5"       # la star sous-cotée par l'ADP
    assert reco.candidates[0].p_now == 1.0                    # c'est mon choix : toujours disponible
    assert reco.standings.shape == (4, len(CATS))
    again = Simulator(league, pool, state, seed=1).recommend()
    assert [c.player.name for c in again.candidates[:5]] == [c.player.name for c in reco.candidates[:5]]


def test_recommendation_need_model_and_finished_draft(monkeypatch):
    pool = _pool(monkeypatch)
    league = _league("need")
    state = build_state(league, pool, [(1, 1, "Joueur 0")])
    assert Simulator(league, pool, state, seed=2).recommend().candidates
    picks = [(r, k, f"Joueur {(r - 1) * 4 + k - 1}") for r in range(1, 5) for k in range(1, 5)]
    done = build_state(league, pool, picks)
    reco = Simulator(league, pool, done).recommend()
    assert reco.current is None and not reco.candidates
