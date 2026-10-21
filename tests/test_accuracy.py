"""Pertinence des sources (scripts.accuracy) sur un historique fabriqué : A exacte, B biaisée, C partielle."""

import pytest

from scripts import accuracy, history

SEASON = "2026-27"
CATS_RATE = {"pts": 0.5, "reb": 0.2, "ast": 0.1, "stl": 0.03, "blk": 0.02, "tov": 0.05, "fg3m": 0.05}


def _player(i):
    f = 1.0 if i % 5 else 0.5                 # part des matchs de l'équipe jouée
    return {"key": f"p{i}", "team": f"T{i % 4}", "f": f, "mpg": 20.0 + i % 15, "k": 1.0 + 0.02 * i}


PLAYERS = [_player(i) for i in range(40)]


def _row(p, gp, mpg, rate_factor=1.0, fgp_shift=0.0):
    minutes = gp * mpg
    row = {"name_key": p["key"], "player": p["key"], "team": p["team"], "gp": gp, "min": minutes, "mpg": mpg}
    for cat, rate in CATS_RATE.items():
        row[cat] = minutes * rate * p["k"] * rate_factor
    row["fga"], row["fta"] = minutes * 0.4, minutes * 0.1
    row["fgp"], row["ftp"] = (0.45 + 0.002 * int(p["key"][1:]) + fgp_shift,
                              0.70 + 0.005 * int(p["key"][1:]) + fgp_shift)
    row["fgm"], row["ftm"] = row["fga"] * row["fgp"], row["fta"] * row["ftp"]
    return row


def _insert(conn, kind, source, stage, as_of, rows):
    sid = conn.execute("INSERT INTO snapshots (kind, source, season, stage, as_of, taken_at, content_hash, n_rows) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (kind, source, SEASON, stage, as_of, as_of, f"{kind}{source}{stage}{as_of}", len(rows))).lastrowid
    cols = ["name_key", "player", "team"] + list(history.FIELDS)
    conn.executemany(f"INSERT INTO snapshot_rows (snapshot_id, {', '.join(cols)}) VALUES (?, {', '.join('?' * len(cols))})",
                     [(sid,) + tuple(r.get(c) for c in cols) for r in rows])


def _sea(team_games):
    return [_row(p, p["f"] * team_games, p["mpg"]) for p in PLAYERS]


@pytest.fixture
def hist_path(tmp_path):
    path = str(tmp_path / "history.sqlite")
    conn = history.connect(path)
    with conn:
        exact = [_row(p, p["f"] * 82, p["mpg"]) for p in PLAYERS]
        biased = [_row(p, 73.8, p["mpg"] + 5, 1.3, 0.05) for p in PLAYERS]
        _insert(conn, "raw", "A", "draft", "2026-10-12 10:00:00", exact)
        _insert(conn, "raw", "B", "draft", "2026-10-12 10:00:00", biased)
        _insert(conn, "raw", "C", "draft", "2026-10-12 10:00:00", exact[:20])
        final = [dict(a, **{k: (a[k] + b[k]) / 2 for k in history.FIELDS if a.get(k) is not None})
                 for a, b in zip(exact, biased)]
        _insert(conn, "final", "draft", "draft", "2026-10-12T12:00:00", final)
        # ros au 1er novembre (10 matchs joués) ; stats réelles au 1er, 16 novembre et 1er décembre
        _insert(conn, "raw", "A", "ros", "2026-11-01 08:00:00", [_row(p, p["f"] * 72, p["mpg"]) for p in PLAYERS])
        _insert(conn, "raw", "B", "ros", "2026-11-01 08:00:00", [_row(p, 60, p["mpg"] + 5, 1.3) for p in PLAYERS])
        _insert(conn, "raw", "A", "l15", "2026-11-01 08:00:00", _sea(5))
        for day, games in (("2026-11-01", 10), ("2026-11-16", 15), ("2026-12-01", 20)):
            _insert(conn, "raw", "FantasyPros", "sea", f"{day} 09:00:00", _sea(games))
    conn.close()
    return path


def _slot(code, source, stage, gp, mn, stats, final=False):
    return {"code": code, "source": source, "season": SEASON, "stage": stage, "final": final,
            "weights": {"gp": gp, "min": mn, "stats": {c: stats for c in accuracy.CATS}}}


GRIDS = {
    "draft": {"slots": [_slot("a", "A", "draft", 0.4, 0.4, 0.4), _slot("b", "B", "draft", 0.4, 0.4, 0.4),
                        _slot("c", "C", "draft", 0.2, 0.2, 0.2)]},
    "lt": {"slots": [_slot("a.ros", "A", "ros", 0.5, 0.5, 0.5), _slot("b.ros", "B", "ros", 0.5, 0.5, 0.5)]},
    "st": {"slots": [_slot("a.l15", "A", "l15", 0.0, 1.0, 1.0), _slot("lt", "lt", "lt", 1.0, 0.0, 0.0, True)]},
}


def _compute(path, shrink=0.5):
    league = {"history": {"shrink": shrink, "players": 200}, "season": {"st_days": 15}}
    return accuracy.compute(path, settings={"active_season": SEASON}, league=league, grids=GRIDS)


def _by_code(level):
    return {r["code"]: r for r in level["rows"]}


def test_exact_source_ranks_first_and_takes_optimal_weight(hist_path):
    res = _compute(hist_path)
    assert res["truth"] == "FantasyPros" and str(res["as_of"]) == "2026-12-01"
    draft = res["phases"]["draft"]
    for level in accuracy.LEVELS:
        rows = _by_code(draft["levels"][level])
        assert rows["a"]["error"] == pytest.approx(0, abs=1e-6), level
        assert rows["b"]["error"] > 0.1 and rows["a"]["rank"] == 1 and rows["b"]["rank"] == 2
        assert rows["c"]["partial"] and rows["c"]["coverage"] == pytest.approx(0.5)
        # optimal : tout le poids des codes comparés (80 %) sur A ; C garde ses 20 %
        assert rows["a"]["optimal"] == pytest.approx(0.8, abs=0.01) and rows["b"]["optimal"] == pytest.approx(0, abs=0.01)
        assert rows["a"]["proposed"] == pytest.approx(0.6, abs=0.01) and rows["c"]["proposed"] == pytest.approx(0.2)
        blends = draft["levels"][level]["blends"]
        assert blends["Mélange proposé"][0] < blends["Mélange actuel"][0]
    by_cat = _by_code(draft["levels"]["stats"])["b"]["by_cat"]
    assert set(by_cat) == set(accuracy.CATS) and by_cat["pts"] > 0


def test_ros_measured_from_start_date_and_shrink_setting(hist_path):
    res = _compute(hist_path, shrink=1.0)
    lt = res["phases"]["lt"]
    assert [(str(d), str(b), str(e)) for d, b, e, _ in lt["periods"]] == [("2026-11-01", "2026-11-01", "2026-12-01")]
    rows = _by_code(lt["levels"]["gp"])
    assert rows["a.ros"]["error"] == pytest.approx(0, abs=1e-6) and rows["b.ros"]["error"] > 0.5
    assert rows["a.ros"]["proposed"] == pytest.approx(0.5)      # rapprochement 100 % : poids actuels


def test_short_term_window_and_report_rows(hist_path):
    res = _compute(hist_path)
    st = res["phases"]["st"]
    assert [(str(b), str(e)) for _, b, e, _ in st["periods"]] == [("2026-11-01", "2026-11-16")]
    assert _by_code(st["levels"]["min"])["a.l15"]["error"] == pytest.approx(0, abs=1e-6)
    table = accuracy.rows(res)
    text = "\n".join(" | ".join("" if c is None else str(c) for c in r) for r in table)
    assert "Grille draft" in text and "couverture partielle" in text and "Mélange proposé" in text
    assert "PTS (écart)" in text


def test_no_history_and_empty_season(tmp_path):
    res = _compute(str(tmp_path / "absent.sqlite"))
    assert "absent" in accuracy.rows(res)[1][0]
    path = str(tmp_path / "vide.sqlite")
    history.connect(path).close()
    text = str(accuracy.rows(_compute(path)))
    assert "pas encore de période comparable" in text


def test_simplex_projection():
    w = accuracy.project_simplex(accuracy.np.array([0.8, 0.6, -0.2]))
    assert w.sum() == pytest.approx(1) and (w >= 0).all()
