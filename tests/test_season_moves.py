"""Simulation de mouvements : échanges (dans les deux sens), tolérance de valeur, joueurs libres, réglages."""

from datetime import date

import numpy as np
import pytest

from scripts import config_sheet as C
from scripts.draft.pool import STATS, Player, Pool
from scripts.season import moves
from scripts.season.engine import project_team, standings_projection

TODAY = date(2026, 10, 31)
GAMES = [(f"2026-11-{d:02d}", "DEN", "LAL") for d in range(1, 11)]
ROSTERS = {"Moi": ["A", "B"], "Rival": ["C", "D"], "Autre": ["E", "F"]}
VALUES = {"A": 5, "B": 2, "C": 9, "D": 3, "E": 6, "F": 5, "G": 4, "H": 1}    # G, H : joueurs libres
# remplacement = 6e meilleur (6 joueurs dans les effectifs) = 3 : valeurs A 2, B 0, C 6, D 0


def _player(idx, name, value):
    per_game = np.zeros(len(STATS))
    per_game[STATS.index("pts")] = value
    return Player(idx=idx, player_id=idx, name=name, team="DEN", positions="C", gp=10, per_game=per_game,
                  value_avg=float(value), value_tot=float(value), rank_tot=idx, adp=None, market=idx)


def _league(players, compare, tolerance=1.0, side="Moi", minimum=0):
    return {"games": {"per_slot": 82}, "roster": {"C": 2}, "draft": {"my_team": "Moi"},
            "season": {"simulations": 50},
            "moves": {"value_tolerance": tolerance, "max_results": 20, "min_results": minimum, "fa_results": 6, "fa_shortlist": 15,
                      "players": [{"manager": side, "player": p} for p in players], "compare": compare}}


def _result(league, cap=20):
    pool = Pool([_player(i, n, v) for i, (n, v) in enumerate(VALUES.items())])
    teams = []
    for manager, names in ROSTERS.items():
        roster = {"players": [{"player": n, "nba_team": "DEN", "positions": "C", "slot": "C"} for n in names],
                  "games": {"C": {"remaining": cap}}}
        teams.append(project_team(manager, manager, roster, None, pool, None, GAMES, TODAY, 15, league))
    cats, weights = ["pts"], [1.0]
    return {"teams": teams, "categories": cats, "weights": weights,
            "projection": standings_projection(teams, cats, weights, sims=50),
            "context": {"lt_pool": pool, "st_pool": None, "games": GAMES, "today": TODAY, "horizon": 15,
                        "league": league}}


def _names(players):
    return [p.name for p in players]


def test_trades_are_sorted_from_most_to_least_balanced():
    league = _league(["B"], {"type": "manager", "manager": "Rival"})
    out = moves.simulate(_result(league), league)
    assert out["tested"] == 2 and out["kept"] == 2
    # B (valeur 0) contre D (0) : écart 0 % ; contre C (60) : 100 %
    assert [_names(m.recv) for m in out["moves"]] == [["D"], ["C"]]
    by_gain = max(out["moves"], key=lambda m: m.my_gain)
    assert _names(by_gain.recv) == ["C"] and by_gain.partner_gain < 0
    assert all(not m.out_of_tolerance for m in out["moves"]) and out["moves"][0].p_first is not None


def test_minimum_results_are_completed_out_of_tolerance():
    league = _league(["A"], {"type": "manager", "manager": "Rival"}, tolerance=0.10)
    out = moves.simulate(_result(league), league)
    # A (valeur 2 x 10 matchs) contre C (6 x 10) : écart 67 % ; contre D (0) : 100 %
    assert out["tested"] == 2 and out["kept"] == 0 and out["moves"] == []
    league["moves"]["min_results"] = 10
    out = moves.simulate(_result(league), league)
    assert [_names(m.recv) for m in out["moves"]] == [["C"], ["D"]]
    assert all(m.out_of_tolerance for m in out["moves"])
    assert "hors tolérance" in [cell for r in moves.rows(out) for cell in r]
    league["moves"]["value_tolerance"] = 0.70
    out = moves.simulate(_result(league), league)
    assert [(m.out_of_tolerance, _names(m.recv)) for m in out["moves"]] == [(False, ["C"]), (True, ["D"])]


def test_players_of_the_other_manager_are_compared_with_mine():
    league = _league(["C"], {"type": "manager", "manager": "Rival"}, side="Rival")
    out = moves.simulate(_result(league), league)
    assert out["tested"] == 2
    assert sorted(_names(m.give)[0] for m in out["moves"]) == ["A", "B"]
    assert all(_names(m.recv) == ["C"] for m in out["moves"])
    assert _names(max(out["moves"], key=lambda m: m.my_gain).give) == ["B"]   # céder B pour C : meilleur gain


def test_received_players_only_get_the_games_my_lineup_gives_them():
    """Plafond de 15 matchs au poste C : A (5) joue 10 matchs, B (2) les 5 restants. Recevoir C (9) contre B :
    C prend 10 matchs, A n'en garde que 5."""
    league = _league(["B"], {"type": "manager", "manager": "Rival"})
    out = moves.simulate(_result(league, cap=15), league)
    move = next(m for m in out["moves"] if _names(m.recv) == ["C"])
    # matchs du 1er au 10 novembre, horizon court de 15 jours : tous en st
    assert move.recv_detail == [("C", 10.0, 10.0, 10.0, 0.0)] and move.lineup_changes == [("A", -5.0)]
    # points : C 9 x 10 + A 5 x 5 au lieu de A 5 x 10 + B 2 x 5 -> +55
    assert move.stat_delta.tolist() == pytest.approx([55.0])
    rows = moves.rows(out)
    header = next(r for r in rows if r and r[0] == "#")
    row = next(r for r in rows if r and r[0] == 1 + out["moves"].index(move))
    assert "C 10/10 (st 10 + lt 0)" in row and "A -5" in row
    assert row[header.index("PTS (stats)")] == 55.0


def test_free_agents_best_replacement_first():
    league = _league(["B"], {"type": "free agent", "manager": ""})
    out = moves.simulate(_result(league), league)
    assert [_names(m.recv) for m in out["moves"]] == [["G"], ["H"]]
    assert out["moves"][0].my_gain > 0 > out["moves"][1].my_gain
    rows = moves.rows(out)
    assert any(r and r[0] == "#" for r in rows) and "J'ajoute" in next(r for r in rows if r and r[0] == "#")


def test_identity_move_has_no_gain():
    league = _league(["B"], {"type": "manager", "manager": "Rival"})
    sim = moves.Simulator(_result(league), league)
    me = sim.index("Moi")
    _, points = sim.evaluate(me, [moves._entry(p) for p in sim.teams[me].players])
    assert abs(points[me].sum() - sim.base_total[me]) < 1e-9


@pytest.mark.parametrize("players,compare,side,message", [
    ([], {"type": "free agent"}, "Moi", "Aucun joueur"),
    (["C"], {"type": "free agent"}, "Rival", "doivent être les miens"),
    (["E"], {"type": "manager", "manager": "Rival"}, "Autre", "attendu Moi ou Rival"),
    (["B"], {}, "Moi", "Moves | Compare"),
    (["Z"], {"type": "manager", "manager": "Rival"}, "Moi", "absent de l'effectif"),
    (["B"], {"type": "manager", "manager": "Inconnu"}, "Moi", "absent des effectifs"),
])
def test_invalid_settings_are_explained(players, compare, side, message):
    league = _league(players, compare, side=side)
    with pytest.raises(moves.MovesError, match=message):
        moves.simulate(_result(league), league)


def test_settings_rows_are_parsed():
    rows = [["Moves | Player 1", "Commish", "Jalen Suggs"], ["Moves | Player 2", "Commish", "Darryn Peterson"],
            ["Moves | Player 3", "Commish", ""], ["Moves | Compare", "manager", "Cliffy"],
            ["Optim | Moves", "Value tolerance", "15%"]]
    cfg, _ = C.parse_rows(C.build_rows() + rows)
    m = cfg["league"]["moves"]
    assert m["players"] == [{"manager": "Commish", "player": "Jalen Suggs"},
                            {"manager": "Commish", "player": "Darryn Peterson"}]
    assert m["compare"] == {"type": "manager", "manager": "Cliffy"} and m["value_tolerance"] == pytest.approx(0.15)
    cfg, _ = C.parse_rows(C.build_rows() + [["Moves | Compare", "Free Agent", ""]])
    assert cfg["league"]["moves"]["compare"] == {"type": "free agent", "manager": ""}
