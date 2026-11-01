"""Fiabilité des GP : projections « modèles » de 9cat écartées, GP plafonné aux matchs restants (phase lt)."""
from datetime import date

from scripts.sources.ninecat import drop_templates
from scripts.weighting.engine import cap_games_left


def _rec(name, gp, mpg):
    return {"player": name, "gp": gp, "mpg": mpg, "min": gp * mpg, "pts": gp * mpg / 2}


def test_drop_templates_only_repeated_30_game_rows():
    records = [_rec("Modèle A", 30, 22.6), _rec("Modèle B", 30, 22.6), _rec("Modèle C", 30, 22.6),
               _rec("Vrai 30", 30, 13.2),            # 30 matchs mais MPG propre : gardé
               _rec("Titulaire 1", 70, 32.7), _rec("Titulaire 2", 70, 32.7), _rec("Titulaire 3", 70, 32.7)]
    kept, dropped = drop_templates(records)
    assert dropped == ["Modèle A", "Modèle B", "Modèle C"]
    assert [r["player"] for r in kept] == ["Vrai 30", "Titulaire 1", "Titulaire 2", "Titulaire 3"]


def test_drop_templates_uses_minutes_when_mpg_missing():
    records = [{"player": f"P{i}", "gp": 30, "mpg": None, "min": 30 * 26.1} for i in range(3)]
    assert len(drop_templates(records)[1]) == 3


def test_cap_games_left_scales_totals():
    games = [(f"2026-11-{d:02d}", "TOR", "XXX") for d in range(1, 21)]          # 20 matchs restants
    results = [{"player": "Jamarion Sharp", "team": "TOR", "gp": 82.0, "pts": 820.0, "min": 1640.0, "fga": None},
               {"player": "Ok", "team": "TOR", "gp": 18.0, "pts": 180.0},
               {"player": "Libre", "team": "FA", "gp": 82.0, "pts": 820.0}]
    assert cap_games_left(results, "lt", games, today=date(2026, 11, 1)) == 1
    sharp, ok, free = results
    assert sharp["gp"] == 20 and sharp["pts"] == 200 and sharp["min"] == 400
    assert ok["gp"] == 18 and free["gp"] == 82


def test_lt_phase_caps_gp(tmp_path, monkeypatch):
    from scripts import db
    from scripts.weighting import engine

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setattr(engine, "export_csv", lambda *a, **k: None)
    monkeypatch.setattr(engine, "load_availability", lambda *a, **k: {})
    today = date.today()
    monkeypatch.setattr(engine, "_games", lambda *a, **k: [(today.isoformat(), "TOR", "XXX")] * 40)
    db.init_db()
    with db.get_connection() as conn, conn:
        conn.execute("INSERT INTO players (player_id, canonical_name, name_key) VALUES (1, 'Sharp', 'sharp')")
        conn.execute("INSERT INTO raw_projections (player_id, player, player_key, team, source, season, stage, "
                     "gp, mpg, pts) VALUES (1, 'sharp', 'sharp', 'TOR', 'FantasyPros', '2026-27', 'ros', 82, 10, 400)")
    engine.compute_phase("lt", {"active_season": "2026-27", "active_stage": "ros"})
    with db.get_connection() as conn:
        assert conn.execute("SELECT gp, pts FROM final_projections WHERE phase='lt'").fetchone() == (40, 195.12)
