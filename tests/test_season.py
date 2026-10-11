from datetime import date

import numpy as np

from scripts.draft.pool import STATS, Player, Pool
from scripts.season import report
from scripts.season.engine import CATEGORY_ORDER, PlayerProjection, TeamProjection, gp_inconsistencies, optimize_team, project_team

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


def _rivals(values, cat="pts"):
    rivals = []
    for i, value in enumerate(values):
        actual = np.zeros(len(STATS))
        actual[STATS.index(cat)] = value
        rivals.append(TeamProjection(f"Rival {i}", f"Rival {i}", [], {}, {}, actual, 0.0,
                                     np.zeros(len(STATS)), {}, None))
    return rivals


def test_st_games_are_allocated_before_lt_when_roster_exceeds_cap():
    pool = Pool([_player(1, "Center One", 10, 5.0), _player(2, "Center Two", 10, 4.0)])
    roster = {"players": [{"player": name, "nba_team": "DEN", "positions": "C", "slot": "C"}
                          for name in ("Center One", "Center Two")],
              "games": {"C": {"remaining": 16}}}
    team = project_team("Moi", "Equipe", roster, None, pool, None, _games(10),
                        date(2026, 10, 31), 5, LEAGUE)
    players = {player.name: player for player in team.players}

    assert players["Center One"].games_st_sched == 4 and players["Center One"].games_lt_sched == 6
    assert players["Center One"].games_st_expected == 4 and players["Center One"].games_lt_expected == 6
    assert players["Center One"].games_st_used == 4 and players["Center One"].games_lt_used == 6
    assert players["Center Two"].games_st_used == 4 and players["Center Two"].games_lt_used == 2
    assert sum(p.games_st_used for p in players.values()) == 8
    assert sum(p.games_lt_used for p in players.values()) == 8
    assert sum(p.games_used for p in players.values()) == 16


def test_st_allocation_prioritizes_recent_value():
    lt_pool = Pool([_player(1, "Center One", 10, 8.0), _player(2, "Center Two", 10, 3.0)])
    st_pool = Pool([_player(1, "Center One", 10, 1.0), _player(2, "Center Two", 10, 5.0)])
    roster = {"players": [{"player": name, "nba_team": "DEN", "positions": "C", "slot": "C"}
                          for name in ("Center One", "Center Two")],
              "games": {"C": {"remaining": 6}}}

    team = project_team("Moi", "Equipe", roster, None, lt_pool, st_pool, _games(10),
                        date(2026, 10, 31), 5, LEAGUE)
    players = {player.name: player for player in team.players}

    assert players["Center Two"].games_st_used == 4
    assert players["Center One"].games_st_used == 2
    assert sum(player.games_lt_used for player in players.values()) == 0


def test_optimizer_chooses_player_that_gains_roto_points_against_opponents():
    lt_players = [_player(1, "Low Scorer", 10, 5.0), _player(2, "High Scorer", 10, 1.0)]
    lt_players[0].per_game[STATS.index("pts")] = 2.0
    lt_players[1].per_game[STATS.index("pts")] = 20.0
    pool = Pool(lt_players)
    roster = {"players": [{"player": name, "nba_team": "DEN", "positions": "C", "slot": "C"}
                          for name in ("Low Scorer", "High Scorer")],
              "games": {"C": {"remaining": 1}}}
    team = project_team("Moi", "Equipe", roster, None, pool, None, _games(10),
                        date(2026, 10, 31), 5, LEAGUE)

    optimize_team(team, _rivals((0.0, 50.0, 100.0)), ["pts"], [1.0])

    players = {player.name: player for player in team.players}
    assert players["High Scorer"].games_used == 1
    assert players["Low Scorer"].games_used == 0
    assert team.remaining[STATS.index("pts")] == 20


def test_optimizer_preserves_scarce_position_bucket_for_single_position_player():
    pts = STATS.index("pts")
    flexible = PlayerProjection("Flexible", "DEN", "G,F", "G", True)
    flexible.games_lt_expected = 1
    flexible.per_game_lt[pts] = 20
    guard = PlayerProjection("Guard", "DEN", "G", "G", True)
    guard.games_lt_expected = 1
    guard.per_game_lt[pts] = 10
    team = TeamProjection("Moi", "Equipe", [flexible, guard], {"G": 1, "F": 1, "C": 0, "UTIL": 0},
                          {}, np.zeros(len(STATS)), 0.0, np.zeros(len(STATS)), {}, None)

    optimize_team(team, _rivals((0.0, 10.0, 30.0)), ["pts"], [1.0])

    assert flexible.games_used == guard.games_used == 1
    assert team.used == {"G": 1, "F": 1, "C": 0.0, "UTIL": 0.0}


def test_optimizer_uses_zero_gain_games_and_breaks_ties_by_player_value():
    to = STATS.index("tov")
    high_value = PlayerProjection("High Value", "DEN", "C", "C", True, value=8.0)
    high_value.games_lt_expected = 1
    high_value.games_expected = 1
    high_value.per_game_lt[to] = 0
    low_value = PlayerProjection("Low Value", "DEN", "C", "C", True, value=2.0)
    low_value.games_lt_expected = 1
    low_value.games_expected = 1
    low_value.per_game_lt[to] = 0
    team = TeamProjection("Moi", "Equipe", [low_value, high_value], {"G": 0, "F": 0, "C": 1, "UTIL": 0},
                          {}, np.zeros(len(STATS)), 0.0, np.zeros(len(STATS)), {}, None)

    optimize_team(team, _rivals((0.0, 0.0, 0.0), "tov"), ["tov"], [1.0])

    assert high_value.games_used == 1
    assert low_value.games_used == 0
    assert high_value.games_left_voluntarily == 0
    assert high_value.games_lost_caps == 0
    assert low_value.games_lost_caps == 1      # plafond C épuisé : perdu, pas laissé


def test_negative_gain_games_are_reported_as_voluntarily_left():
    to = STATS.index("tov")
    player = PlayerProjection("Turnover", "DEN", "C", "C", True, value=1.0)
    player.games_lt_expected = 1
    player.games_expected = 1
    player.per_game_lt[to] = 1
    team = TeamProjection("Moi", "Equipe", [player], {"G": 0, "F": 0, "C": 1, "UTIL": 0},
                          {}, np.zeros(len(STATS)), 0.0, np.zeros(len(STATS)), {}, None)

    optimize_team(team, _rivals((0.0, 0.0, 0.0), "tov"), ["tov"], [1.0])
    rows = report.my_roster_rows(team, ["tov"])

    assert player.games_used == 0
    assert player.games_lost_caps == 0
    assert player.games_left_voluntarily == 1
    assert rows[1][12:14] == ["Perdus (plafonds)", "Laissés volontairement"]
    assert rows[2][12:14] == [0, 1]


def test_optimizer_keeps_playing_when_categories_are_saturated():
    """Très loin devant : gain nul (à l'arrondi près) à chaque match, mais tous les matchs sont joués."""
    pts = STATS.index("pts")
    players = []
    for i in range(3):
        p = PlayerProjection(f"P{i}", "DEN", "C", "C", True, value=3.0 - i)
        p.games_lt_expected = p.games_expected = 20
        p.per_game_lt[pts] = 25.0 + i * 0.1
        players.append(p)
    actual = np.zeros(len(STATS))
    actual[pts] = 1e6
    team = TeamProjection("Moi", "Equipe", players, {"G": 0, "F": 0, "C": 60, "UTIL": 0},
                          {}, actual, 0.0, np.zeros(len(STATS)), {}, None)

    optimize_team(team, _rivals((100.0, 200.0, 300.0)), ["pts"], [1.0])

    assert sum(p.games_used for p in players) == 60
    assert all(p.games_left_voluntarily == 0 and p.games_lost_caps == 0 for p in players)


def test_optimizer_score_does_not_drift(monkeypatch):
    """Le score courant est recalculé : un très léger écart d'arrondi n'arrête pas la boucle."""
    pts = STATS.index("pts")
    players = []
    for i in range(2):
        p = PlayerProjection(f"P{i}", "DEN", "C", "C", True, value=2.0 - i)
        p.games_lt_expected = p.games_expected = 10
        p.per_game_lt[pts] = 0.1 + i * 1e-3
        players.append(p)
    team = TeamProjection("Moi", "Equipe", players, {"G": 0, "F": 0, "C": 20, "UTIL": 0},
                          {}, np.zeros(len(STATS)), 0.0, np.zeros(len(STATS)), {}, None)

    optimize_team(team, _rivals((1e5, 2e5, 3e5)), ["pts"], [1.0])

    assert sum(p.games_used for p in players) == 20


def test_roster_report_includes_st_and_lt_allocations():
    pool = Pool([_player(1, "Center", 10, 5.0)])
    roster = {"players": [{"player": "Center", "nba_team": "DEN", "positions": "C", "slot": "C"}],
              "games": {"C": {"remaining": 6}}}
    team = project_team("Moi", "Equipe", roster, None, pool, None, _games(10),
                        date(2026, 10, 31), 5, LEAGUE)

    rows = report.roster_rows({"teams": [team]})

    assert rows[0][7:13] == [
        "Matchs calendrier ST", "Matchs attendus ST", "Matchs affectés ST",
        "Matchs calendrier LT", "Matchs attendus LT", "Matchs affectés LT",
    ]
    assert rows[1][7:13] == [4, 4.0, 4.0, 6, 6.0, 2.0]
    assert rows[0][15:17] == ["Perdus (plafonds)", "Laissés volontairement"]


def test_season_reco_shows_all_rosters_once_and_opponents_block():
    pool = Pool([_player(1, "Center", 10, 5.0)])
    roster = {"players": [{"player": "Center", "nba_team": "DEN", "positions": "C", "slot": "C"}],
              "games": {"C": {"remaining": 6}}}
    team = project_team("Moi", "Equipe", roster, None, pool, None, _games(10),
                        date(2026, 10, 31), 5, LEAGUE)
    projection = {
        "expected": np.array([0.0]), "p_first": np.array([0.0]), "p_podium": np.array([0.0]),
        "points": np.zeros((1, 0)), "values": np.zeros((1, 0)),
    }

    rows = report.season_rows(
        {"teams": [team], "categories": [], "projection": projection, "today": date(2026, 10, 31),
         "my_team": "Autre"},
        {"season": {"st_days": 5}},
    )

    titles = [row[0] for row in rows if row]
    assert titles.count("Effectifs des 15 équipes : matchs ST / LT par joueur") == 1
    assert sum("Matchs calendrier ST" in row for row in rows) == 1
    assert any(str(t).startswith("Adversaires : taux d'alignement") for t in titles)


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


def test_optimizer_fills_caps_at_start_of_season():
    """Début de saison : stats réelles nulles, adversaires projetés sur toute la saison.
    Mesurer le gain à partir des seules stats réelles laissait presque tous les matchs (seul le coût en TO
    comptait) ; il est mesuré autour du total final projeté."""
    rng = np.random.default_rng(0)

    def per_game():
        v = np.zeros(len(STATS))
        for cat, x in {"pts": 15, "reb": 5, "ast": 4, "stl": 1, "blk": .6, "tov": 2, "fga": 12, "fta": 4,
                       "fg3m": 1.8}.items():
            v[STATS.index(cat)] = x * rng.uniform(.5, 1.5)
        v[STATS.index("fgm")] = v[STATS.index("fga")] * rng.uniform(.42, .58)
        v[STATS.index("ftm")] = v[STATS.index("fta")] * rng.uniform(.65, .9)
        return v

    players = []
    for i in range(13):
        p = PlayerProjection(f"P{i}", "DEN", ["G", "F", "C", "G,F", "F,C"][i % 5], "G", True, value=13.0 - i)
        p.games_st_expected, p.games_lt_expected = 5.0, 60.0
        p.games_expected = 65.0
        p.per_game_st = p.per_game_lt = per_game()
        players.append(p)
    caps = {"G": 164.0, "F": 164.0, "C": 82.0, "UTIL": 246.0}
    team = TeamProjection("Moi", "Equipe", players, caps, {}, np.zeros(len(STATS)), 0.0,
                          sum(p.per_game_lt * 50 for p in players), {}, None)
    rivals = [TeamProjection(f"Rival {k}", "R", [], {}, {}, sum(per_game() for _ in range(13)) * 50, 0.0,
                             np.zeros(len(STATS)), {}, None) for k in range(14)]

    optimize_team(team, rivals, list(CATEGORY_ORDER), [1.0] * len(CATEGORY_ORDER))

    assert sum(p.games_used for p in players) == sum(caps.values())
    assert sum(p.games_left_voluntarily for p in players) == 0
