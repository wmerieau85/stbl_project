from datetime import date

import numpy as np

from scripts.draft.pool import STATS, Player, Pool
from scripts.season.engine import gp_inconsistencies, project_team

LEAGUE = {"games": {"per_slot": 82}, "roster": {"G": 0, "F": 0, "C": 1, "UTIL": 0}}


def _player(idx, name, gp, value, positions="C"):
    return Player(idx=idx, player_id=idx, name=name, team="DEN", positions=positions, gp=gp,
                  per_game=np.ones(len(STATS)), value_avg=value, value_tot=value, rank_tot=idx, adp=None,
                  market=idx)


def _games(n):
    return [(f"2026-11-{d:02d}", "DEN", "LAL") for d in range(1, n + 1)]


def test_caps_give_games_to_best_player_first():
    pool = Pool([_player(1, "Big One", 10, 5.0), _player(2, "Big Two", 10, 1.0)])
    roster = {"players": [{"player": n, "nba_team": "DEN", "positions": "C", "slot": "C"}
                          for n in ("Big Two", "Big One")], "games": {"C": {"remaining": 12}}}
    team = project_team("Moi", "Equipe", roster, None, pool, None, _games(10), date(2026, 10, 31), 15, LEAGUE)
    used = {p.name: p.games_used for p in team.players}
    assert used["Big One"] == 10 and used["Big Two"] == 2      # plafond C = 12
    assert team.used["C"] == 12


def test_gp_inconsistency_detected():
    pool = Pool([_player(1, "Full Season", 70, 1.0), _player(2, "Remaining", 8, 1.0)])
    roster = {"players": [{"player": n, "nba_team": "DEN", "positions": "C", "slot": "C"}
                          for n in ("Full Season", "Remaining")], "games": {}}
    team = project_team("Moi", "Equipe", roster, None, pool, None, _games(10), date(2026, 10, 31), 15, LEAGUE)
    assert [n for n, _, _ in gp_inconsistencies([team])] == ["Full Season"]


def test_pool_find_accents_and_lost_chars():
    pool = Pool([_player(1, "Nikola Jokic", 70, 5.0), _player(2, "Luka Doncic", 70, 5.0)])
    assert pool.find("Nikola Jokić").name == "Nikola Jokic"
    assert pool.find("Nikola Joki?").name == "Nikola Jokic"
    assert pool.find("Luka Don?i?").name == "Luka Doncic"
