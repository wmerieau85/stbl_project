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
        assert v["draft"] + v["calendar"] == pytest.approx(v["expected"])
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
    assert by["C2"]["status"] == "parti" and by["D"]["status"] == "arrivé" and by["D"]["fa"] >= 0
    assert by["D"]["trade"] == 0 and res["moves"] == []          # sans transactions : joueurs libres, pas de bilan
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


def test_draft_games_from_bdd_replace_the_assistant_rotation():
    """Rotation de draft saisie dans bdd (GP draft) : A2 sur le banc (0 match prévu) -> attendu de X plus bas."""
    header = ["Team Draft", "Team Season", "Phase", "Player"] + [""] * 3 + ["GP draft"]
    rows = [header, ["X", "X", "draft", "A1"] + [""] * 3 + ["82"], ["X", "X", "draft", "A2"] + [""] * 3 + [""],
            ["X", "X", "lt", "A1"] + [""] * 3 + ["70"], ["Y", "Y", "draft", "B1"] + [""] * 3 + ["60,5"]]
    games = analysis.read_draft_games(rows)
    assert games == {"X": {"A1": 82.0, "A2": 0.0}, "Y": {"B1": 60.5}}
    assert analysis.read_draft_games([["Team Draft", "Phase", "Player"], ["X", "draft", "A1"]]) is None
    res, log = _league()
    draft = Pool([_p(1, "A1", 82, 20, 5, 3), _p(2, "A2", 82, 10, 8, 2), _p(3, "B1", 82, 18, 6, 3),
                  _p(4, "B2", 82, 12, 7, 2), _p(5, "C1", 82, 15, 9, 3), _p(6, "C2", 82, 11, 4, 2)])
    real = Pool([_p(k, n, 10, 10, 5) for k, n in enumerate(["A1", "A2", "B1", "B2", "C1", "C2", "D"], 1)])
    picks = {"X": ["A1", "A2"], "Y": ["B1", "B2"], "Z": ["C1", "C2"]}
    rosters = {"X": ["A1", "A2"], "Y": ["B1", "B2"], "Z": ["C1", "C2"]}
    bench = analysis.analyze(draft, real, picks, rosters, log, 82 * 4, CATS, W,
                             draft_games={"X": {"A1": 82, "A2": 0}})
    a2 = next(d for d in bench["detail"] if d["player"] == "A2")
    assert a2["expected_games"] == 0                              # sur le banc dans la rotation de draft
    for v in bench["summary"].values():
        assert v["draft"] + v["calendar"] == pytest.approx(v["expected"])


def _dated():
    """X échange A2 à Y contre B2 le 2026-10-25 (4 matchs joués) ; Z lâche C2 pour D le 2026-10-28 (6 matchs)."""
    draft = Pool([_p(1, "A1", 82, 20, 5, 3), _p(2, "A2", 82, 10, 8, 2), _p(3, "B1", 82, 18, 6, 3),
                  _p(4, "B2", 82, 12, 7, 2), _p(5, "C1", 82, 15, 9, 3), _p(6, "C2", 82, 11, 4, 2)])
    real = Pool([_p(1, "A1", 10, 20, 5), _p(2, "A2", 10, 10, 8), _p(3, "B1", 10, 25, 6), _p(4, "B2", 10, 12, 7),
                 _p(5, "C1", 10, 15, 9), _p(6, "C2", 10, 11, 4), _p(7, "D", 10, 14, 6)])
    picks = {"X": ["A1", "A2"], "Y": ["B1", "B2"], "Z": ["C1", "C2"]}
    rosters = {"X": ["A1", "B2"], "Y": ["B1", "A2"], "Z": ["C1", "D"]}
    log = {"X": {"A1": _log(10, 200, 50), "A2": _log(4, 40, 32), "B2": _log(6, 72, 42)},
           "Y": {"B1": _log(10, 250, 60), "B2": _log(4, 48, 28), "A2": _log(6, 60, 48)},
           "Z": {"C1": _log(10, 150, 90), "C2": _log(3, 33, 12), "D": _log(4, 56, 24)}}
    tx = [{"date": "2026-10-25", "type": "trade", "manager": "Y", "other_manager": "X", "player": "A2",
           "transaction": "t1"},
          {"date": "2026-10-25", "type": "trade", "manager": "X", "other_manager": "Y", "player": "B2",
           "transaction": "t1"},
          {"date": "2026-10-28", "type": "add", "manager": "Z", "other_manager": "", "player": "D",
           "transaction": "t2"},
          {"date": "2026-10-28", "type": "drop", "manager": "Z", "other_manager": "", "player": "C2",
           "transaction": "t2"}]
    history = {n.lower(): [("2026-10-21", 0), ("2026-10-25", 4), ("2026-10-28", 6), ("2026-11-02", 10)]
               for n in ["A1", "A2", "B1", "B2", "C1", "C2", "D"]}
    timeline = analysis.Timeline(history, today="2026-11-02")
    return analysis.analyze(draft, real, picks, rosters, log, 82 * 4, CATS, W, transactions=tx, timeline=timeline)


def test_dated_moves_split_trades_and_free_agents():
    res = _dated()
    for v in res["summary"].values():
        steps = sum(v[s] for s, _ in analysis.STEPS)
        assert v["expected"] + steps + v["rest"] == pytest.approx(v["real"])
    by = {(d["manager"], d["player"]): d for d in res["detail"]}
    assert by[("X", "A2")]["status"] == "parti (échange)" and by[("X", "A2")]["owned"] == 4
    assert by[("X", "B2")]["status"] == "arrivé (échange)" and by[("X", "B2")]["owned"] == 6
    assert by[("Z", "C2")]["status"] == "parti (joueur libre)" and by[("Z", "C2")]["owned"] == 6
    assert by[("Z", "C2")]["rotation"] < 0          # 3 matchs comptés sur 6 joués chez Z : banc
    assert by[("Z", "D")]["status"] == "arrivé (joueur libre)" and by[("Z", "D")]["trade"] == 0
    assert by[("X", "A1")]["rotation"] == 0         # tous ses matchs comptés


def test_move_review_by_transaction():
    res = _dated()
    moves = {(d["manager"], d["date"]): d for d in res["moves"]}
    assert set(moves) == {("X", "2026-10-25"), ("Y", "2026-10-25"), ("Z", "2026-10-28")}
    z = moves[("Z", "2026-10-28")]
    assert z["type"] == "joueur libre" and z["in"] == ["D"] and z["out"] == ["C2"]
    assert moves[("X", "2026-10-25")]["type"] == "échange" and moves[("X", "2026-10-25")]["out"] == ["A2"]
    rows = analysis.rows(res, me="Z")
    start = rows.index(analysis.MOVES_HEADER)
    assert rows[start + 1][:5] == ["Z", "2026-10-28", "joueur libre", "D", "C2"]
    assert len(rows[rows.index(analysis.DETAIL_HEADER) + 1]) == len(analysis.DETAIL_HEADER)


def test_read_transactions_block_in_chronological_order():
    from scripts.sheets import column_index
    from scripts.yahoo import sheets as ys

    start = column_index(ys.TRANSACTIONS_START_COL)
    header = [""] * start + ys.TRANSACTIONS_HEADER

    def row(day, kind, manager, player, tid, other=""):
        values = {"date": day, "type": kind, "manager": manager, "player": player, "transaction": tid,
                  "other_manager": other}
        return [""] * start + [values.get(h, "") for h in ys.TRANSACTIONS_HEADER]

    rows = [header, row("28/10/2026", "add", "Z", "D", "t3"), row("2026-10-25", "drop", "X", "A1", "t2"),
            row("2026-10-25", "add", "X", "A1", "t1"), row("2026-10-24", "waiver", "X", "E", "t0")]
    tx = analysis.read_transactions(rows)
    assert [(t["date"], t["transaction"]) for t in tx] == [("2026-10-25", "t1"), ("2026-10-25", "t2"),
                                                           ("2026-10-28", "t3")]
    assert analysis.read_transactions([["date"]]) is None


def test_timeline_history_then_calendar_then_days():
    t = analysis.Timeline({"a": [("2026-10-21", 0), ("2026-10-25", 3)]}, today="2026-10-31")
    assert t.before(["a"], "2026-10-24", 8) == 0 and t.before(["a"], "2026-10-26", 8) == 3
    assert t.before(["a"], "9999-12-31", 8) == 8
    games = [("2026-10-21", "BOS", "NYK"), ("2026-10-23", "BOS", "MIA"), ("2026-10-27", "BOS", "LAL"),
             ("2026-10-29", "CHI", "BOS")]
    t = analysis.Timeline(games=games, today="2026-10-31")
    assert t.before(["x"], "2026-10-25", 4, "BOS") == 2            # 2 matchs de BOS sur 4 avant le 25
    t = analysis.Timeline(today="2026-10-31", start="2026-10-21")
    assert t.before(["x"], "2026-10-26", 10) == pytest.approx(5)    # moitié des jours
