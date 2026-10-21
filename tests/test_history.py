"""Photos des projections et stats réelles (scripts.history)."""

import os
import sqlite3

import pytest

from scripts import cloud, history


def _main_db(path, rows, final=None):
    """Base principale minimale : players, raw_projections [(source, stage, player_id, pts, imported_at)]."""
    conn = sqlite3.connect(path)
    cols = ", ".join(f"{f} REAL" for f in history.FIELDS)
    conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS players (player_id INTEGER PRIMARY KEY, canonical_name TEXT, name_key TEXT);
        CREATE TABLE IF NOT EXISTS raw_projections (player_id INTEGER, player TEXT, team TEXT, positions TEXT,
            source TEXT, season TEXT, stage TEXT, {cols}, imported_at TEXT);
        CREATE TABLE IF NOT EXISTS final_projections (phase TEXT, season TEXT, player_id INTEGER, player TEXT,
            team TEXT, positions TEXT, {cols}, computed_at TEXT);
        DELETE FROM raw_projections; DELETE FROM final_projections; DELETE FROM players;
    """)
    conn.executemany("INSERT INTO players VALUES (?, ?, ?)", [(1, "A", "a"), (2, "B", "b")])
    for source, stage, pid, pts, at in rows:
        conn.execute("INSERT INTO raw_projections (player_id, player, team, positions, source, season, stage, gp, "
                     "pts, imported_at) VALUES (?, ?, 'BOS', 'G', ?, '2026-27', ?, ?, ?, ?)",
                     (pid, "AB"[pid - 1], source, stage, 2 if stage == "sea" else 70, pts, at))
    for phase, pid, pts, at in final or []:
        conn.execute("INSERT INTO final_projections (phase, season, player_id, player, gp, pts, computed_at) "
                     "VALUES (?, '2026-27', ?, ?, 70, ?, ?)", (phase, pid, "AB"[pid - 1], pts, at))
    conn.commit()
    conn.close()


def _states(report):
    return {(kind, source, stage): state for kind, source, stage, state, _ in report}


@pytest.fixture
def hist(tmp_path):
    conn = history.connect(str(tmp_path / "history.sqlite"))
    yield conn
    conn.close()


def test_snapshot_only_when_content_changes_and_interval_elapsed(tmp_path, hist):
    main = str(tmp_path / "main.sqlite")
    league = {"history": {"projections_days": 7, "stats_days": 1}}
    _main_db(main, [("CBS", "draft", 1, 1500, "2026-10-12 10:00:00"), ("CBS", "ros", 1, 1400, "2026-10-18 10:00:00"),
                    ("9cat", "sea", 1, 0, "2026-10-18 10:00:00")], final=[("lt", 1, 1450, "2026-10-18T12:00:00")])
    first = _states(history.take(main, hist, league))
    assert set(first.values()) == {"photographiée"} and len(first) == 4
    assert _states(history.take(main, hist, league))[("raw", "CBS", "ros")] == "inchangée"
    # le lendemain : nouvelles projections ros (trop tôt), nouvelles stats réelles, nouvelles projections draft
    _main_db(main, [("CBS", "draft", 1, 1510, "2026-10-19 10:00:00"), ("CBS", "ros", 1, 1390, "2026-10-19 10:00:00"),
                    ("9cat", "sea", 1, 25, "2026-10-19 10:00:00")], final=[("lt", 1, 1450, "2026-10-18T12:00:00")])
    second = _states(history.take(main, hist, league))
    assert second[("raw", "CBS", "ros")] == "moins de 7 j depuis la précédente"
    assert second[("raw", "9cat", "sea")] == "photographiée" and second[("raw", "CBS", "draft")] == "photographiée"
    assert second[("final", "lt", "lt")] == "inchangée"
    _main_db(main, [("CBS", "ros", 1, 1380, "2026-10-25 10:00:00")])
    assert _states(history.take(main, hist, league))[("raw", "CBS", "ros")] == "photographiée"
    counts = dict(((s, st), n) for _, s, _, st, n, *_ in history.status(hist))
    assert counts == {("CBS", "draft"): 2, ("CBS", "ros"): 2, ("9cat", "sea"): 2, ("lt", "lt"): 1}
    pts = [p for (p,) in hist.execute("SELECT r.pts FROM snapshot_rows r JOIN snapshots s USING (snapshot_id) "
                                      "WHERE s.source='CBS' AND s.stage='ros' ORDER BY s.as_of")]
    assert pts == [1400, 1380]
    assert hist.execute("SELECT DISTINCT name_key FROM snapshot_rows").fetchall() == [("a",)]


def test_interval_rules():
    conf = {"projections_days": 7, "stats_days": 1}
    assert history.interval_days("raw", "draft", conf) == 0
    assert history.interval_days("raw", "sea", conf) == 1
    assert [history.interval_days("raw", s, conf) for s in ("ros", "l30", "l07")] == [7, 7, 7]
    assert history.interval_days("final", "st", conf) == 7 and history.interval_days("final", "draft", conf) == 0


def test_retention_keeps_most_recent_seasons(hist):
    for season in ("2024-25", "2025-26", "2026-27"):
        sid = hist.execute("INSERT INTO snapshots (kind, source, season, stage, as_of, taken_at, content_hash, n_rows) "
                           "VALUES ('raw', 'CBS', ?, 'ros', '2026-01-01', '2026-01-01', ?, 1)",
                           (season, season)).lastrowid
        hist.execute("INSERT INTO snapshot_rows (snapshot_id, player_id) VALUES (?, 1)", (sid,))
    assert history.purge(hist, 2) == ["2024-25"]
    assert [s for (s,) in hist.execute("SELECT season FROM snapshots ORDER BY season")] == ["2025-26", "2026-27"]
    assert hist.execute("SELECT COUNT(*) FROM snapshot_rows").fetchone() == (2,)


def test_online_import_uses_a_temporary_copy_without_drive_sync(tmp_path):
    main = str(tmp_path / "main.sqlite")
    _main_db(main, [("CBS", "ros", 1, 1400, "2026-10-18 10:00:00")])
    seen = {}

    def step(args, env):
        seen.update(args=args, env=env)
        _main_db(env["STBL_DB_PATH"], [("CBS", "ros", 1, 1300, "2026-10-20 09:00:00")])
        return 0

    tmp = history.fetch_online(main, step=step)
    assert tmp != main and seen["env"]["STBL_DB_PATH"] == tmp and seen["env"][cloud.RUNNER_ENV] == "1"
    assert "--skip-weighting" in seen["args"] and "--stage" in seen["args"]
    read = lambda p: sqlite3.connect(p).execute("SELECT pts FROM raw_projections").fetchone()[0]
    assert read(main) == 1400 and read(tmp) == 1300
    os.remove(tmp)


def test_real_stats_of_previous_season_are_ignored(tmp_path, hist):
    main = str(tmp_path / "main.sqlite")
    _main_db(main, [("9cat", "sea", 1, 2000, "2026-10-18 10:00:00")])
    with sqlite3.connect(main) as conn:
        conn.execute("UPDATE raw_projections SET gp = 82")                # 82 matchs au 18 octobre : saison passée
    assert _states(history.take(main, hist, {}))[("raw", "9cat", "sea")] == "stats d'une autre saison, ignorée"
    assert history.status(hist) == []
