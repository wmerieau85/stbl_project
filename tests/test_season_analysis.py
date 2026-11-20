"""Analyse draft -> saison à date : étapes additives, axes par joueur, lecture de l'onglet yahoo."""
import numpy as np
import pytest

from scripts.draft.pool import STATS, Player, Pool
from scripts.season import analysis

CATS, W = ["pts", "reb"], [1.0, 1.0]


def _p(i, name, gp, pts, reb, value=0.0, team="T1"):
    per_game = np.zeros(len(STATS))
    per_game[STATS.index("pts")], per_game[STATS.index("reb")] = pts, reb
    return Player(idx=i, player_id=i, name=name, team=team, positions="G", gp=gp, per_game=per_game,
                  value_avg=value, value_tot=value, rank_tot=i, adp=None, market=i)


def _log(gp, pts, reb):
    t = np.zeros(len(STATS))
    t[STATS.index("pts")], t[STATS.index("reb")] = pts, reb
    return gp, t


def _league():
    draft = Pool([_p(1, "A1", 82, 20, 5, 3), _p(2, "A2", 82, 10, 8, 2), _p(3, "B1", 82, 18, 6, 3),
                  _p(4, "B2", 82, 12, 7, 2), _p(5, "C1", 82, 15, 9, 3), _p(6, "C2", 82, 11, 4, 2)])
    # 10 matchs joués par l'équipe NBA ; A1 blessé (2 matchs) ; B1 en sur-régime ; D libre en début de saison
    real = Pool([_p(1, "A1", 2, 20, 5), _p(2, "A2", 10, 10, 8), _p(3, "B1", 10, 25, 6), _p(4, "B2", 10, 5, 7),
                 _p(5, "C1", 10, 15, 9), _p(6, "C2", 10, 11, 4), _p(7, "D", 10, 14, 6)])
    picks = {"X": ["A1", "A2"], "Y": ["B1", "B2"], "Z": ["C1", "C2"]}
    rosters = {"X": ["A1", "A2"], "Y": ["B1", "B2"], "Z": ["C1", "D"]}            # Z : C2 lâché, D ajouté
    log = {"X": {"A1": _log(2, 40, 10), "A2": _log(10, 100, 80)},
           "Y": {"B1": _log(10, 250, 60), "B2": _log(10, 50, 70)},
           "Z": {"C1": _log(10, 150, 90), "C2": _log(3, 33, 12), "D": _log(7, 98, 42)}}
    return analysis.analyze(draft, real, picks, rosters, log, 82 * 4, CATS, W), log


def test_steps_add_up_from_draft_reco_to_yahoo_standings():
    res, log = _league()
    for m, v in res["summary"].items():
        assert v["reco"] + v["unit"] + v["calendar"] == pytest.approx(v["expected"])
        steps = sum(v[s] for s, _ in analysis.STEPS)
        assert v["expected"] + steps + v["rest"] == pytest.approx(v["real"])
    totals = np.array([[sum(t[STATS.index(c)] for _, t in log[m].values()) for c in CATS] for m in res["managers"]])
    real = analysis.exact_points(totals, CATS, W)
    assert [res["summary"][m]["real"] for m in res["managers"]] == pytest.approx(list(real))


def test_axes_by_player():
    res, _ = _league()
    by = {d["player"]: d for d in res["detail"]}
    assert by["A1"]["injury"] < 0 and by["A1"]["status"] == "drafté"            # 2 matchs sur 10 prévus
    assert by["B1"]["perf"] > 0                                                 # 25 pts au lieu de 18 : 1er en PTS
    assert by["B2"]["perf"] == 0          # 5 pts au lieu de 12, mais Y reste 1er en PTS : aucun point roto perdu
    assert by["C2"]["status"] == "parti" and by["D"]["status"] == "arrivé" and by["D"]["moves"] >= 0
    assert by["A2"]["expected_games"] == pytest.approx(10.0)                    # 82 matchs x 10/82
    assert res["summary"]["X"]["injury"] < 0


def test_rows_layout():
    res, _ = _league()
    rows = analysis.rows(res, me="Z")
    assert rows[3] == analysis.SUMMARY_HEADER and len(rows[4]) == len(analysis.SUMMARY_HEADER)
    detail = rows.index(analysis.DETAIL_HEADER)
    assert rows[detail + 1][0] == "Z"                                           # mon équipe d'abord
    assert any(r and r[0] == "Z (moi)" for r in rows[4:detail])


def test_read_yahoo_tab_blocks():
    from scripts.sheets import column_index
    from scripts.yahoo import sheets as ys

    width = column_index("BH") + 1
    header = [""] * width
    for block, names in ((ys.DRAFT_START_COL, ["pick", "round", "pick_in_round", "team", "manager", "player"]),
                         (ys.ROSTERS_START_COL, ["team_id", "team", "manager", "player"]),
                         (ys.TEAM_LOG_START_COL, ys.TEAM_LOG_HEADER)):
        for k, n in enumerate(names):
            header[column_index(block) + k] = n
    row = [""] * width
    row[column_index("A") + 4], row[column_index("A") + 5] = "X", "A1"
    row[column_index("L") + 2], row[column_index("L") + 3] = "X", "A2"
    log_start = column_index(ys.TEAM_LOG_START_COL)
    values = {"manager": "X", "player": "A1", "GP": "12", "PTS": "1 234,5", "REB": "80", "TO": "20"}
    for k, n in enumerate(ys.TEAM_LOG_HEADER):
        row[log_start + k] = values.get(n, "")
    picks, rosters, log = analysis.read_yahoo_tab([header, row])
    assert picks == {"X": ["A1"]} and rosters == {"X": ["A2"]}
    gp, totals = log["X"]["A1"]
    assert gp == 12 and totals[STATS.index("pts")] == 1234.5 and totals[STATS.index("tov")] == 20
