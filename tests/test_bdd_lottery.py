import random
from types import SimpleNamespace

import pytest

from scripts import bdd
from scripts.draft.state import picks_from_assignments
from scripts.lottery import generate_balls, summary
from scripts.names import name_key

GS = {"projections_start_col": "C"}
ROWS = [
    ["Team Draft", "Team Season", "Phase", "Player"],
    ["Tof", "", "draft", "Nikola Jokic"],
    ["Colo", "Thomas", "draft", "Victor Wembanyama"],
    ["", "", "draft", "Luka Doncic"],
    ["", "", "lt", "Nikola Jokic"],
    ["", "Tof", "lt", "Luka Doncic"],
]


def test_layout_and_team_map():
    assert bdd.layout(GS) == {"phase": 2, "player": 3, "team_draft": 0, "team_season": 1}
    mapping = bdd.team_map(ROWS, GS)
    assert mapping[name_key("Nikola Jokic")] == ("Tof", "")
    assert mapping[name_key("Victor Wembanyama")] == ("Colo", "Thomas")
    assert mapping[name_key("Luka Doncic")] == ("", "Tof")
    block = [["Phase", "Player"], ["st", "Luka Doncic"], ["draft", "Nikola Jokic"]]
    assert bdd.team_columns(block, mapping) == [["Team Draft", "Team Season"], ["", "Tof"], ["Tof", ""]]


def test_assignments():
    assert bdd.draft_assignments(ROWS, GS) == [("Tof", "Nikola Jokic"), ("Colo", "Victor Wembanyama")]
    pairs, column = bdd.season_assignments(ROWS, GS)
    assert column == "Team Season" and sorted(pairs) == [("Thomas", "Victor Wembanyama"), ("Tof", "Luka Doncic")]
    pairs, column = bdd.season_assignments([r[:1] + [""] + r[2:] for r in ROWS], GS)
    assert column == "Team Draft" and len(pairs) == 2


class Pool:
    def find(self, name):
        return SimpleNamespace(idx=name_key(name), name=name)


def test_picks_from_assignments_keepers_first():
    league = {"draft": {"order": ["A", "B", "C"], "rounds": 4, "keeper_rounds": [1, 2],
                        "keepers": {"A": ["K1", "K2"]}}}
    rows = picks_from_assignments(league, Pool(), [("A", "P1"), ("A", "K2"), ("B", "P2"), ("A", "K1")])
    # A : choix 1 (tour 1) et 6 (tour 2, snake) pour ses keepers, P1 au tour 3 ; B : tour 3 (pas de keeper déclaré)
    assert rows == [(1, 1, "K2"), (2, 3, "K1"), (3, 1, "P1"), (3, 2, "P2")]


def test_lottery_balls():
    managers = ["Thomas", "Joris", "Colo", "Commish"]
    active = [15, 0, "0", 0]
    balls = [["7", ""], ["327", ""], ["667", ""], ["", "26"]]
    result = generate_balls(managers, active, balls, 1, random.Random(1))
    assert len(result) == 994 and [b for b, _ in result] == list(range(1, 995))
    assert {m for _, m in result} == {"Joris", "Colo"}
    assert dict((m, n) for m, n, _ in summary(result)) == {"Colo": 667, "Joris": 327}
    assert len(generate_balls(managers, active, balls, 2)) == 26
    with pytest.raises(ValueError):
        generate_balls(managers, active, balls, 3)
