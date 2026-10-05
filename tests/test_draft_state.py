import numpy as np

from scripts.draft.pool import Player, Pool
from scripts.draft.state import build_state


def _player(i, name):
    return Player(idx=i, player_id=i, name=name, team="X", positions="C", gp=70, per_game=np.ones(11),
                  value_avg=0, value_tot=0, rank_tot=i + 1, adp=None, market=i + 1)


def _league(keepers=None):
    return {"teams": 2, "draft": {"order": ["A", "B"], "rounds": 3, "my_team": "A", "type": "snake",
                                  "keepers": keepers or {}}}


def test_duplicate_pick_not_counted_twice(monkeypatch):
    monkeypatch.setattr("scripts.draft.pool.load_aliases", lambda: {})
    pool = Pool([_player(0, "Nikola Jokic"), _player(1, "Luka Doncic")])
    state = build_state(_league(), pool, [(1, 1, "Nikola Jokic"), (1, 2, "Nikola Jokic"), (2, 1, "Luka Doncic")])
    rosters = state.rosters()
    assert [p.name for p in rosters["A"]] == ["Nikola Jokic"]
    assert [p.name for p in rosters["B"]] == ["Luka Doncic"]
    assert len(state.picks) == 3 and state.picks[1].player is None   # le choix reste occupé
    assert state.duplicates and "déjà choisi au tour 1, choix 1" in state.duplicates[0]


def test_keeper_picked_by_other_team_ignored(monkeypatch):
    monkeypatch.setattr("scripts.draft.pool.load_aliases", lambda: {})
    pool = Pool([_player(0, "Nikola Jokic")])
    state = build_state(_league({"A": ["Nikola Jokic"]}), pool, [(1, 2, "Nikola Jokic")])
    assert [p.name for p in state.rosters()["A"]] == ["Nikola Jokic"] and state.rosters()["B"] == []
    assert "keeper de A" in state.duplicates[0]
