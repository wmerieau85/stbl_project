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


# --- Recherche auto -----------------------------------------------------------------------------

AUTO_ROSTERS = {"Moi": ["P1", "P2", "R1"], "Rival": ["R2", "R3", "P3"], "Autre": ["M1", "M2", "M3"]}
# P = points, R = rebonds, M = mixte ; (pts, reb, valeur) ; Z joueur libre : remplacement = 9e = 5
AUTO_STATS = {"P1": (20, 2, 6), "P2": (18, 2, 5.5), "R1": (4, 6, 5), "R2": (4, 12, 6), "R3": (4, 11, 5.5),
              "P3": (12, 2, 5), "M1": (12, 7, 6), "M2": (11, 7, 5.5), "M3": (12, 6, 5), "Z": (1, 1, 1)}


def _auto_result(league, cap=30):
    players = []
    for i, (name, (pts, reb, value)) in enumerate(AUTO_STATS.items()):
        per_game = np.zeros(len(STATS))
        per_game[STATS.index("pts")], per_game[STATS.index("reb")] = pts, reb
        players.append(Player(idx=i, player_id=i, name=name, team="DEN", positions="C", gp=10, per_game=per_game,
                              value_avg=float(value), value_tot=float(value), rank_tot=i, adp=None, market=i))
    pool = Pool(players)
    teams = []
    for manager, names in AUTO_ROSTERS.items():
        roster = {"players": [{"player": n, "nba_team": "DEN", "positions": "C", "slot": "C"} for n in names],
                  "games": {"C": {"remaining": cap}}}
        teams.append(project_team(manager, manager, roster, None, pool, None, GAMES, TODAY, 15, league))
    cats, weights = ["pts", "reb"], [1.0, 1.0]
    return {"teams": teams, "categories": cats, "weights": weights,
            "projection": standings_projection(teams, cats, weights, sims=50),
            "context": {"lt_pool": pool, "st_pool": None, "games": GAMES, "today": TODAY, "horizon": 15,
                        "league": league}}


def _auto_league(manager=""):
    league = _league([], {"type": "auto", "manager": manager}, tolerance=0.10)
    league["roster"] = {"C": 3}
    league["moves"].update(auto_max_players=2, auto_shortlist=40, auto_min_partner_gain=0.0)
    return league


def test_auto_finds_win_win_trades_without_players():
    league = _auto_league()
    out = moves.simulate(_auto_result(league), league)
    assert out["mode"] == "auto" and out["partners"] == ["Rival", "Autre"] and out["moves"]
    assert all(m.my_gain > 0 and m.partner_gain >= 0 for m in out["moves"])
    # du plus équitable au moins équitable
    assert [abs(m.value_diff) for m in out["moves"]] == sorted(abs(m.value_diff) for m in out["moves"])
    best = out["moves"][0]
    # j'ai trop de points et pas de rebonds : je cède un marqueur contre un rebondeur
    assert best.partner_name == "Rival" and _names(best.give)[0].startswith("P") and "R2" in _names(best.recv)
    rows = moves.rows(out)
    header = next(r for r in rows if r and r[0] == "#")
    assert header[1] == "Manager" and "Gain roto (partenaire)" in header
    assert next(r for r in rows if r and r[0] == 1)[1] == "Rival"
    assert "Rival" in moves.console(out)


def test_auto_can_target_one_manager_and_respects_partner_gain():
    league = _auto_league("Autre")
    out = moves.simulate(_auto_result(league), league)
    assert out["partners"] == ["Autre"] and all(m.partner_name == "Autre" for m in out["moves"])
    league = _auto_league()
    league["moves"]["auto_min_partner_gain"] = 100.0
    out = moves.simulate(_auto_result(league), league)
    assert out["moves"] == [] and "aucun échange gagnant-gagnant" in str(moves.rows(out))


def test_auto_screen_is_close_to_the_exact_simulation():
    league = _auto_league("Rival")
    sim = moves.Simulator(_auto_result(league), league)
    me, partner = sim.index("Moi"), sim.index("Rival")
    found, tested = moves._screen(sim, me, partner, 1, 1.0)
    assert tested == 9 and len(found) == 9
    for gain, partner_gain, give, recv in found:
        mine = [moves._entry(x) for x in sim.teams[me].players if x is not give[0]] + [moves._entry(recv[0])]
        theirs = [moves._entry(x) for x in sim.teams[partner].players if x is not recv[0]] + [moves._entry(give[0])]
        _, points = sim.evaluate(me, mine, partner, theirs)
        assert gain == pytest.approx(points[me].sum() - sim.base_total[me], abs=0.05)


def test_auto_setting_is_parsed():
    cfg, _ = C.parse_rows(C.build_rows() + [["Moves | Compare", "Auto", ""],
                                            ["Optim | Moves", "Auto max players", "3"]])
    m = cfg["league"]["moves"]
    assert m["compare"] == {"type": "auto", "manager": ""} and m["auto_max_players"] == 3


def test_auto_screen_respects_full_lineups():
    # plafond de 20 matchs pour 3 joueurs : R1 est remplaçant chez moi, P3 chez Rival
    league = _auto_league("Rival")
    sim = moves.Simulator(_auto_result(league, cap=20), league)
    me, partner = sim.index("Moi"), sim.index("Rival")
    found = {(give[0].name, recv[0].name): gain for gain, _, give, recv in moves._screen(sim, me, partner, 1, 1.0)[0]}
    # céder un remplaçant contre un joueur qui ne serait pas titulaire ne rapporte rien (avant : tous ses matchs
    # attendus étaient ajoutés)
    assert found[("R1", "P3")] == pytest.approx(0.0, abs=0.05)
    assert found[("P2", "P3")] == pytest.approx(0.0, abs=0.05)
    # céder un titulaire contre un meilleur joueur : même gain que la simulation exacte
    give = next(x for x in sim.teams[me].players if x.name == "P2")
    recv = next(x for x in sim.teams[partner].players if x.name == "R2")
    mine = [moves._entry(x) for x in sim.teams[me].players if x is not give] + [moves._entry(recv)]
    theirs = [moves._entry(x) for x in sim.teams[partner].players if x is not recv] + [moves._entry(give)]
    _, points = sim.evaluate(me, mine, partner, theirs)
    assert found[("P2", "R2")] == pytest.approx(points[me].sum() - sim.base_total[me], abs=0.05)


def test_auto_completes_with_trades_where_partner_loses():
    league = _auto_league()
    league["moves"]["min_results"] = 10
    out = moves.simulate(_auto_result(league, cap=20), league)
    assert out["win_win"] == 0 and out["moves"]
    assert all(m.partner_loses and m.my_gain > 0 and m.partner_gain < 0 for m in out["moves"])
    rows = moves.rows(out)
    header = next(r for r in rows if r and r[0] == "#")
    first = next(r for r in rows if r and r[0] == 1)
    assert first[header.index("Accord")] == "il perd des points"
    assert "[il perd des points]" in moves.console(out)


def test_auto_shows_one_trade_per_effect_on_my_team():
    league = _auto_league()
    league["moves"]["min_results"] = 10
    sim_out = moves.simulate(_auto_result(league, cap=20), league)
    sim = moves.Simulator(_auto_result(league, cap=20), league)
    me = sim.index("Moi")
    cores = [moves._core(sim, me, m) for m in sim_out["moves"]]
    assert len(cores) == len(set(cores))


def test_auto_interleaves_trade_sizes():
    assert moves._interleave([[1, 2, 3], ["a"], ["x", "y"]]) == [1, "a", "x", 2, "y", 3]


def _result_slots(league, slots, cap=20):
    """Comme _result, avec le poste Yahoo de mes joueurs (ex. IL)."""
    result = _result(league, cap)
    pool = result["context"]["lt_pool"]
    roster = {"players": [{"player": n, "nba_team": "DEN", "positions": "C", "slot": slots.get(n, "C")}
                          for n in ROSTERS["Moi"]], "games": {"C": {"remaining": cap}}}
    result["teams"][0] = project_team("Moi", "Moi", roster, None, pool, None, GAMES, TODAY, 15, league)
    return result


def test_auto_free_agents_find_who_to_drop_and_who_to_add():
    """Moi : A (5) et B (2), plafond plein ; libres : G (4), H (1). Seul « lâcher B, prendre G » fait gagner."""
    league = _league([], {"type": "auto free agent", "manager": ""})
    out = moves.simulate(_result(league), league)
    assert out["mode"] == "auto free agent" and out["drops"] == 2 and out["free_count"] == 2
    assert out["tested"] == 4
    assert [(_names(m.give), _names(m.recv)) for m in out["moves"]] == [(["B"], ["G"])]
    assert out["moves"][0].my_gain > 0 and out["moves"][0].stat_delta.tolist() == pytest.approx([20.0])
    rows = moves.rows(out)
    assert any("recherche auto de joueurs libres" in str(c) for r in rows for c in r)
    assert "lâcher B, prendre G" in moves.console(out)


def test_auto_free_agents_never_drop_an_injured_reserve_player():
    """B en poste IL : le lâcher ne libère pas de place ; lâcher A pour G fait perdre : rien à proposer."""
    league = _league([], {"type": "auto free agent", "manager": ""})
    out = moves.simulate(_result_slots(league, {"B": "IL"}), league)
    assert out["drops"] == 1 and out["moves"] == []
    assert any("aucun joueur libre ne me fait gagner" in str(c) for r in moves.rows(out) for c in r)


@pytest.mark.parametrize("param,value", [("Auto Free Agent", ""), ("auto", "auto free agent"), ("auto", "Free Agent"),
                                         ("free agent", "auto"), ("", "auto free agent"),
                                         ("manager", "auto free agent")])
def test_auto_free_agent_setting_is_read(param, value):
    """Le choix peut être saisi dans la colonne Paramètre ou (en tout ou partie) dans la colonne Valeur."""
    cfg, _ = C.parse_rows(C.build_rows() + [["Moves | Compare", param, value]])
    assert cfg["league"]["moves"]["compare"] == {"type": "auto free agent", "manager": ""}


def test_auto_with_a_manager_is_still_a_trade_search():
    cfg, _ = C.parse_rows(C.build_rows() + [["Moves | Compare", "auto", "Cliffy"]])
    assert cfg["league"]["moves"]["compare"] == {"type": "auto", "manager": "Cliffy"}
