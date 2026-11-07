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


def test_exact_source_ranks_first_and_takes_optimal_weight(hist_path, monkeypatch):
    monkeypatch.setattr(accuracy, "WEAK_PROGRESS", 0.1)     # jeu de test : 24 % de la saison joués
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


def test_short_term_window_and_report_rows(hist_path, monkeypatch):
    monkeypatch.setattr(accuracy, "WEAK_PROGRESS", 0.1)
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


def test_weak_sample_keeps_current_weights(hist_path):
    res = _compute(hist_path)                               # 24 % de la saison : sous WEAK_PROGRESS
    for phase in ("draft", "lt"):
        assert res["phases"][phase]["weak"]
        for level in accuracy.LEVELS:
            lv = res["phases"][phase]["levels"][level]
            assert "échantillon encore trop faible" in lv["verdict"] and "Mélange proposé" not in lv["blends"]
            assert all(r["optimal"] is None and r["proposed"] == r["current"] for r in lv["rows"])
    assert "pas de poids proposés" in str(accuracy.rows(res))


def test_noise_level_difference_is_not_a_conclusion(tmp_path, monkeypatch):
    """Deux sources quasi identiques (écart de 0,1 match) : pas de nouveaux poids."""
    monkeypatch.setattr(accuracy, "WEAK_PROGRESS", 0.1)
    path = str(tmp_path / "h.sqlite")
    conn = history.connect(path)
    with conn:
        for src, shift in (("A", 0.0), ("B", 0.1)):
            _insert(conn, "raw", src, "draft", "2026-10-12 10:00:00",
                    [_row(p, p["f"] * 82 + (shift if i % 2 else -shift), p["mpg"]) for i, p in enumerate(PLAYERS)])
        for day, games in (("2026-11-01", 10), ("2026-12-01", 20)):
            _insert(conn, "raw", "FantasyPros", "sea", f"{day} 09:00:00", _sea(games))
    conn.close()
    grids = dict(GRIDS, draft={"slots": [_slot("a", "A", "draft", 0.5, 0.5, 0.5),
                                         _slot("b", "B", "draft", 0.5, 0.5, 0.5)]})
    res = accuracy.compute(path, settings={"active_season": SEASON},
                           league={"history": {"shrink": 0.5}, "season": {"st_days": 15}}, grids=grids)
    lv = res["phases"]["draft"]["levels"]["gp"]
    assert not lv["verdict"].startswith("proposition")
    assert all(r["proposed"] == r["current"] for r in lv["rows"])


def test_player_dropped_by_a_source_counts_as_zero_games(hist_path):
    conn = history.connect(hist_path)
    with conn:     # B retire p1 (projeté à sa draft) de ses projections ros
        sid = conn.execute("SELECT snapshot_id FROM snapshots WHERE source='B' AND stage='ros'").fetchone()[0]
        conn.execute("DELETE FROM snapshot_rows WHERE snapshot_id=? AND name_key='p1'", (sid,))
    conn.close()
    lt = _compute(hist_path)["phases"]["lt"]
    with accuracy.closing(accuracy.sqlite3.connect(hist_path)) as c:
        hist = accuracy.History(c, SEASON)
        codes = [dict(s, label=s["source"]) for s in GRIDS["lt"]["slots"]]
        seas = hist.of("raw", "FantasyPros", "sea")
        samples, *_ = accuracy.build_samples(hist, "lt", codes, seas, 15, 200)
    pred = next(s["preds"]["b.ros"] for s in samples if s["key"] == "p1")
    assert pred["dropped"] and pred["share"] == 0.0 and pred["mpg"] is None
    assert _by_code(lt["levels"]["gp"])["b.ros"]["coverage"] == pytest.approx(1.0)


def test_injured_players_left_out_of_lt_gp(hist_path):
    from datetime import date, datetime

    conn = history.connect(hist_path)
    state = history.take_injuries(conn, {"p0": "season", "p5": date(2026, 12, 15), "p6": date(2026, 10, 20)},
                                  SEASON, now=datetime(2026, 11, 1, 6, 0))
    assert state == "photographiée"
    assert history.take_injuries(conn, {"p0": "season", "p5": date(2026, 12, 15), "p6": date(2026, 10, 20)},
                                 SEASON, now=datetime(2026, 11, 2, 6, 0)) == "inchangée"
    conn.close()
    lt = _compute(hist_path)["phases"]["lt"]
    assert lt["injured"] == [(date(2026, 11, 1), 2)]        # p0 (saison) et p5 (retour après le 1/11) ; pas p6
    gp_players = _by_code(lt["levels"]["gp"])["a.ros"]["players"]
    assert gp_players == len(PLAYERS) - 2
    assert "blessés écartés de la mesure du GP" in str(accuracy.rows(_compute(hist_path)))
