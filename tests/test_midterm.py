"""Phase mt (moyen terme, vue seulement) : mélange st / lt par match, matchs de la fenêtre J+15 à J+45."""
from datetime import date, timedelta

import pytest

from scripts.weighting import midterm

TODAY = date(2026, 11, 13)
CONF = {"enabled": True, "start_days": 15, "end_days": 45, "st_weight": 0.5}


def _games(days=100, team="OKC"):
    """Un match par jour à partir d'aujourd'hui."""
    return [((TODAY + timedelta(days=d)).isoformat(), team, "XXX") for d in range(days)]


def _row(name, gp, pts, team="OKC", phase="lt"):
    return {"phase": phase, "season": "2026-27", "player_id": 1, "player": name, "team": team, "positions": "G",
            "gp": gp, "mpg": 30.0, "min": 30.0 * gp, "pts": pts, "reb": gp * 5.0, "ast": gp * 4.0,
            "stl": gp * 1.0, "blk": gp * 0.5, "tov": gp * 2.0, "fg3m": gp * 2.0, "fg3a": gp * 5.0,
            "fga": gp * 15.0, "fgm": gp * 7.5, "fta": gp * 4.0, "ftm": gp * 3.2}


def test_blend_and_games_in_window():
    lt = {1: _row("Joueur", 50, 1000.0)}                   # 20 pts / match, joue 50 des 100 matchs restants
    st = {1: _row("Joueur", 7, 210.0, phase="st")}         # 30 pts / match
    [r] = midterm.build(lt, st, _games(), {}, CONF, TODAY)
    assert r["phase"] == "mt" and r["sources"] == "lt,st"
    assert r["gp"] == pytest.approx(31 * 0.5)             # 31 matchs du 15e au 45e jour, joue 1 sur 2
    assert r["pts"] / r["gp"] == pytest.approx(25.0)      # 50 % forme récente, 50 % reste de saison
    assert r["fgp"] == pytest.approx(0.5) and r["ftp"] == pytest.approx(0.8)


def test_injured_player_counts_games_after_return_only():
    lt = {1: _row("Blessé", 35, 700.0)}                    # de retour au 30e jour : 70 matchs restants
    availability = {"blesse": TODAY + timedelta(days=30)}
    [r] = midterm.build(lt, {}, _games(), availability, CONF, TODAY)
    assert r["gp"] == pytest.approx(16 * 35 / 70)          # matchs du 30e au 45e jour, joue 1 sur 2
    assert r["pts"] / r["gp"] == pytest.approx(20.0)       # pas de phase st : lt seule
    assert midterm.build(lt, {}, _games(), {"blesse": "season"}, CONF, TODAY) == []
    assert midterm.build(lt, {}, _games(), {"blesse": TODAY + timedelta(days=60)}, CONF, TODAY) == []


def test_free_agent_and_no_games_ignored():
    assert midterm.build({1: _row("Libre", 20, 400.0, team="FA")}, {}, _games(), {}, CONF, TODAY) == []
    assert midterm.build({1: _row("Sans match", 0, 0.0)}, {}, _games(), {}, CONF, TODAY) == []


def test_params():
    assert midterm.params({}) == CONF
    conf = midterm.params({"season": {"midterm": {"st_weight": "30", "enabled": "non", "end_days": "60"}}})
    assert conf["st_weight"] == pytest.approx(0.3) and conf["enabled"] is False and conf["end_days"] == 60


def test_settings_rows_are_read():
    from scripts import config_sheet as C

    cfg, _ = C.parse_rows(C.build_rows() + [["Optim | Season", "Mid-term st weight", "40%"],
                                            ["Optim | Season", "Mid-term end (days)", "30"]])
    assert cfg["league"]["season"]["midterm"]["st_weight"] == pytest.approx(0.4)
    assert cfg["league"]["season"]["midterm"]["end_days"] == 30


def test_compute_stores_mt_phase(tmp_path, monkeypatch):
    from scripts import db
    from scripts.weighting import engine

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setattr(engine, "export_csv", lambda *a, **k: None)
    monkeypatch.setattr(engine, "load_availability", lambda *a, **k: {})
    monkeypatch.setattr(engine, "_games", lambda *a, **k: _games())
    db.init_db()
    with db.get_connection() as conn, conn:
        engine.ensure_table(conn)
    rows = []
    for k in range(30):                                   # assez de joueurs pour les z-scores
        lt, st = _row(f"P{k}", 50, 1000.0 + 10 * k), _row(f"P{k}", 7, 210.0, phase="st")
        lt["player_id"] = st["player_id"] = k + 1
        rows += [lt, st]
    from scripts.config import load_league

    for phase in ("lt", "st"):
        engine.store_phase(phase, "2026-27", [dict(r, fga_estimated=0) for r in rows if r["phase"] == phase],
                           load_league(), "test")
    assert midterm.compute({"active_season": "2026-27"}, today=TODAY) == 30
    with db.get_connection() as conn:
        gp, rank = conn.execute("SELECT gp, rank_tot FROM final_projections WHERE phase='mt' AND player='P29'").fetchone()
    assert gp == pytest.approx(15.5) and rank == 1
