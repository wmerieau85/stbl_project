"""L'étape active (Actual Phase) ne recalcule que ses phases ; une phase sans données garde le calcul précédent."""
from scripts import db
from scripts.weighting import engine


def _db(tmp_path, monkeypatch):
    path = str(tmp_path / "t.sqlite")
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    with db.get_connection() as conn, conn:
        engine.ensure_table(conn)
        conn.execute("INSERT INTO final_projections (phase, season, player_id, player) VALUES ('draft', '2026-27', 1, 'A')")
        conn.execute("INSERT INTO final_projections (phase, season, player_id, player) VALUES ('lt', '2026-27', 1, 'A')")
    return path


def test_active_stage_selects_its_phases(monkeypatch):
    called = []
    monkeypatch.setattr(engine, "compute_phase", lambda phase, settings: called.append(phase))
    phases = {"draft": ["draft"], "ros": ["lt", "st"]}
    engine.run_phases({"active_stage": "draft", "phases": phases})
    assert called == ["draft"]
    called.clear()
    engine.run_phases({"active_stage": "ros", "phases": phases})
    assert called == ["lt", "st"]


def test_empty_phase_keeps_previous(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    settings = {"active_season": "2026-27", "active_stage": "ros"}
    assert engine.compute_phase("lt", settings) == 0              # aucune donnée ros dans la base vide
    with db.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM final_projections WHERE phase='lt'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM final_projections WHERE phase='draft'").fetchone()[0] == 1
