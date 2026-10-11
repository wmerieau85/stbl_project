from datetime import date, timedelta

import numpy as np

from scripts.config import season_reco_tab
from scripts.draft.pool import STATS
from scripts.season import opponents
from scripts.season.engine import PlayerProjection, TeamProjection, apply_lineup_rate

PTS = STATS.index("pts")
# saison fictive : 100 jours, chaque équipe NBA joue un jour sur deux
GAMES = [((date(2026, 10, 1) + timedelta(days=d)).isoformat(), "DEN", "LAL") for d in range(0, 100, 2)]


def _team(manager, actual_gp, past=20, rest=30, cap=40.0):
    p = PlayerProjection(f"{manager} star", "DEN", "C", "C", True, value=5.0, games_st_sched=5,
                         games_lt_sched=rest - 5, games_past_sched=past, p_play=1.0)
    p.games_st_expected, p.games_lt_expected = 5.0, float(rest - 5)
    p.games_expected = float(rest)
    p.per_game_st = np.zeros(len(STATS))
    p.per_game_st[PTS] = 20.0
    p.per_game_lt = p.per_game_st.copy()
    return TeamProjection(manager, f"Team {manager}", [p], {"G": 0.0, "F": 0.0, "C": cap, "UTIL": 0.0},
                          {}, np.zeros(len(STATS)), float(actual_gp), np.zeros(len(STATS)), {}, None)


def test_apply_lineup_rate_scales_expected_games_then_caps():
    team = _team("A", 0)
    apply_lineup_rate(team, 0.8)
    p = team.players[0]
    assert p.games_st_expected == 4.0 and p.games_lt_expected == 20.0
    assert p.games_used == 24.0
    assert team.remaining[PTS] == 480.0


def test_calibrate_blends_manager_rate_with_league_and_spares_my_team():
    teams = [_team("Moi", 20)] + [_team(f"R{i}", gp) for i, gp in enumerate((20, 18, 16, 14, 10))]
    today = date(2026, 11, 12)          # 42 jours après le début : confiance 100 %
    league = opponents.calibrate(teams, GAMES, today, my_manager="Moi", conf={"opponent_ramp_days": 42})

    by = {t.manager: t for t in teams}
    assert abs(league - 0.85) < 1e-9     # médiane de 1, 1, 0.9, 0.8, 0.7, 0.5
    assert by["Moi"].lineup_rate == 1.0 and by["Moi"].players[0].games_expected == 30.0
    assert by["R4"].lineup["observed"] == 0.5
    assert by["R4"].lineup_rate == 0.5
    assert by["R4"].players[0].games_expected == 15.0


def test_calibrate_early_season_uses_league_default():
    teams = [_team(f"R{i}", 2, past=2) for i in range(6)]       # trop peu de matchs possibles
    opponents.calibrate(teams, GAMES, date(2026, 10, 5), conf={"opponent_default_rate": 0.9})
    assert all(t.lineup_rate == 0.9 and t.lineup["observed"] is None for t in teams)


def test_calibrate_value_model_keeps_all_expected_games():
    teams = [_team(f"R{i}", 10) for i in range(6)]
    opponents.calibrate(teams, GAMES, date(2026, 11, 12), conf={"opponent_model": "value"})
    assert all(t.lineup_rate == 1.0 and t.players[0].games_expected == 30.0 for t in teams)


def test_possible_games_limited_by_cap_pace():
    team = _team("A", 0, past=40, cap=10.0)      # 40 matchs NBA joués mais plafond total de 10
    assert opponents.possible_games_to_date(team, 0.5) == 5.0


def test_season_reco_tab_name_and_legacy_values():
    assert season_reco_tab({"google_sheets": {}}) == "season_reco"
    assert season_reco_tab({"google_sheets": {"season_tab": "season"}}) == "season_reco"
    assert season_reco_tab({"google_sheets": {"season_reco_tab": "season"}}) == "season_reco"
    assert season_reco_tab({"google_sheets": {"season_reco_tab": "ma_saison"}}) == "ma_saison"
