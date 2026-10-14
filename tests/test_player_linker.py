"""Alias appliqués après coup, doublons probables, noms de référence pour Yahoo."""

from scripts import db, player_linker


def _setup(tmp_path, monkeypatch, aliases):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setattr(player_linker, "load_aliases", lambda: aliases)
    db.init_db()


def _raw(conn, player, source, team="LAL"):
    from scripts.names import name_key
    conn.execute("INSERT INTO raw_projections (player, player_key, team, source, season, stage) "
                 "VALUES (?, ?, ?, ?, '2026-27', 'draft')", (player, name_key(player), team, source))


def test_alias_added_later_relinks_existing_rows(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {})
    with db.get_connection() as conn, conn:
        for source in ("cbs", "fp"):
            _raw(conn, "Sandro Mamukelashvili", source)
        _raw(conn, "Alexander Mamukelashvili", "nc")
        _raw(conn, "Franz Wagner", "cbs", "ORL")
        _raw(conn, "Moritz Wagner", "cbs", "ORL")
    player_linker.link_players(season="2026-27", stage="draft")
    dup = player_linker.suspect_duplicates("2026-27", "draft")
    assert list(dup) == [("mamukelashvili", "LAL")]          # les Wagner sont dans la même source : pas signalés

    monkeypatch.setattr(player_linker, "load_aliases",
                        lambda: {"Alexander Mamukelashvili": "Sandro Mamukelashvili"})
    player_linker.link_players(season="2026-27", stage="draft")
    with db.get_connection() as conn:
        ids = {r[0] for r in conn.execute("SELECT player_id FROM raw_projections WHERE player LIKE '%Mamuk%'")}
    assert len(ids) == 1
    assert player_linker.suspect_duplicates("2026-27", "draft") == {}


def test_canonical_names_use_aliases_and_base(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {"Alexander Mamukelashvili": "Sandro Mamukelashvili"})
    with db.get_connection() as conn, conn:
        conn.execute("INSERT INTO players (canonical_name, name_key) VALUES ('Sandro Mamukelashvili', "
                     "'sandro mamukelashvili'), ('Nikola Jokic', 'nikola jokic'), ('Jaren Jackson Jr.', 'jaren jackson')")
    out = player_linker.canonical_names(["Alexander Mamukelashvili", "Nikola Jokić", "Jaren Jackson Jr", "Inconnu"])
    assert out == {"Alexander Mamukelashvili": "Sandro Mamukelashvili", "Nikola Jokić": "Nikola Jokic",
                   "Jaren Jackson Jr": "Jaren Jackson Jr.", "Inconnu": None}
