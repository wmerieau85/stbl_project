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
        # un match joué : le joueur, projeté par une seule source, n'est pas retiré (drop_unreliable)
        conn.execute("INSERT INTO raw_projections (player_id, player, player_key, team, source, season, stage, "
                     "gp, mpg, pts) VALUES (1, 'sharp', 'sharp', 'TOR', 'FantasyPros', '2026-27', 'sea', 1, 10, 5)")
    engine.compute_phase("lt", {"active_season": "2026-27", "active_stage": "ros"})
    with db.get_connection() as conn:
        assert conn.execute("SELECT gp, pts FROM final_projections WHERE phase='lt'").fetchone() == (40, 195.12)


def _loaded():
    def slot(code):
        return {"code": code, "weights": {"gp": 0.5, "min": 0.5, "stats": {"pts": 0.5}}}
    fp = {1: {"player": "Deux sources", "team": "OKC"}, 2: {"player": "Libre", "team": "FA"},
          4: {"player": "Joue déjà", "team": "BOS"}}
    nc = {1: {"player": "Deux sources", "team": "OKC"}, 3: {"player": "Isolé", "team": "TOR"},
          2: {"player": "Libre", "team": ""}, 5: {"player": "Isolé FA", "team": "FA"}}
    return [(slot("fp.ros"), fp), (slot("nc.ros"), nc)]


def test_drop_unreliable():
    from scripts.weighting.engine import drop_unreliable

    loaded = _loaded()
    removed = drop_unreliable(loaded, "lt", played={4, 5}, min_sources=2)
    # Isolé : 1 source, aucun match ; Libre / Isolé FA : sans équipe NBA (même avec des matchs joués)
    assert removed == ["Isolé", "Isolé FA", "Libre"]
    assert set(loaded[0][1]) == {1, 4} and set(loaded[1][1]) == {1}
    assert drop_unreliable(_loaded(), "lt", played={3, 4}, min_sources=2) == ["Isolé FA", "Libre"]   # ont joué
    assert drop_unreliable(_loaded(), "lt", played=set(), min_sources=1) == ["Isolé FA", "Libre"]    # FA seuls


def test_drop_unreliable_keeps_injured_players():
    from scripts.weighting.engine import drop_unreliable

    removed = drop_unreliable(_loaded(), "lt", played=set(), min_sources=2, keep={"isole": "season"})
    assert "Isolé" not in removed and "Libre" in removed


def test_drop_unreliable_counts_only_sources_with_an_nba_team():
    """Kadary Richmond : 9cat (CLE, 3 matchs) + RotoWire sans équipe = une seule source qui compte."""
    from scripts.weighting.engine import drop_unreliable

    def loaded():
        slot = {"code": "x", "weights": {"gp": 0.5, "min": 0.5, "stats": {"pts": 0.5}}}
        nc = {7: {"player": "Kadary Richmond", "team": "CLE", "gp": 3}}
        rw = {7: {"player": "Kadary Richmond", "team": ""}}
        return [(dict(slot, code="nc.ros"), nc), (dict(slot, code="rw.ros"), rw)]

    data = loaded()
    assert drop_unreliable(data, "lt", played=set(), min_sources=2) == ["Kadary Richmond"]
    assert all(7 not in d for _, d in data)
    assert drop_unreliable(loaded(), "lt", played={7}, min_sources=2) == []          # premier match : revient
    assert drop_unreliable(loaded(), "lt", played=set(), min_sources=1) == []        # 1 source suffit


def test_run_phases_removes_retired_mt_phase(tmp_path, monkeypatch):
    """La vue moyen terme (phase mt) a été retirée : ses lignes sont effacées de la base au calcul suivant,
    et l'envoi vers bdd peut la retirer de l'onglet sans --drop-phases."""
    from scripts import db
    from scripts.draft.__main__ import RETIRED_PHASES
    from scripts.weighting import engine

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setattr(engine, "compute_phase", lambda *a, **k: 0)
    db.init_db()
    with db.get_connection() as conn, conn:
        engine.ensure_table(conn)
        conn.execute("INSERT INTO final_projections (phase, season, player_id, player) VALUES ('mt', '2026-27', 1, 'x')")
        conn.execute("INSERT INTO final_projections (phase, season, player_id, player) VALUES ('lt', '2026-27', 1, 'x')")
    engine.run_phases({"active_season": "2026-27", "active_stage": "ros", "phases": {"ros": ["lt", "st"]}})
    with db.get_connection() as conn:
        assert [r[0] for r in conn.execute("SELECT DISTINCT phase FROM final_projections")] == ["lt"]
    assert "mt" in RETIRED_PHASES
