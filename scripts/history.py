"""Photos datées des projections et des stats réelles (fichier history.sqlite), pour mesurer la pertinence
des sources en fin de saison (ou à date).

    python -m scripts.history snapshot      # photographie la base principale (imports et projections finales)
    python -m scripts.history daily         # sources en ligne importées dans une copie temporaire de la base,
                                            # photographiées, puis base principale photographiée
    python -m scripts.history status        # photos disponibles par source / étape

Une photo n'est gardée que si son contenu a changé depuis la précédente, et au plus une tous les N jours
(Optim | History : 1 jour pour les stats réelles de la saison à date (sea), qui servent de résultat de
référence ; 7 jours pour les projections ros, les fenêtres l30 / l15 / l07 et les projections finales lt / st ;
les projections draft à chaque changement). La date d'une photo est
celle de l'import (ou du calcul) des données, pas celle de la photo.
"""

import argparse
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from datetime import datetime

from scripts.config import BASE_DIR, HISTORY_PATH, load_league, load_settings

log = logging.getLogger("stbl.history")

FIELDS = ("gp", "min", "mpg", "pts", "reb", "ast", "stl", "blk", "tov", "fg3m", "fg3a",
          "fgm", "fga", "fgp", "ftm", "fta", "ftp")
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,              -- raw (projection ou stats d'une source) / final (projection pondérée)
    source TEXT NOT NULL,            -- CBS, FantasyPros... ou la phase (draft, lt, st) pour final
    season TEXT NOT NULL,
    stage TEXT NOT NULL,             -- draft, ros, sea, l30... ; phase pour final
    as_of TEXT NOT NULL,             -- date / heure de l'import ou du calcul des données
    taken_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    n_rows INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_key ON snapshots (kind, source, season, stage, as_of);
CREATE TABLE IF NOT EXISTS snapshot_rows (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots (snapshot_id) ON DELETE CASCADE,
    player_id INTEGER,
    name_key TEXT,                   -- clé du joueur (players.name_key), stable d'une base à l'autre
    player TEXT,
    team TEXT,
    positions TEXT,
    {", ".join(f"{f} REAL" for f in FIELDS)}
);
CREATE INDEX IF NOT EXISTS idx_snapshot_rows ON snapshot_rows (snapshot_id);
"""


def connect(path=None):
    conn = sqlite3.connect(path or HISTORY_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def _conf(league=None):
    conf = dict({"projections_days": 7, "stats_days": 1, "retention_seasons": 3})
    conf.update((league or {}).get("history") or {})
    return conf


def interval_days(kind, stage, conf):
    """Écart minimal entre deux photos : 0 pour draft (chaque changement compte)."""
    if stage == "draft":
        return 0
    if kind == "raw" and stage == "sea":
        return int(conf.get("stats_days", 1))
    return int(conf.get("projections_days", 7))


def _day(text):
    return datetime.fromisoformat(str(text).replace("T", " ")[:19]).date()


def _groups(main):
    """[(kind, source, season, stage, as_of, lignes)] de la base principale (joueurs rapprochés seulement)."""
    cols = ", ".join(f"r.{f}" for f in FIELDS)
    out = []
    raw = main.execute("SELECT source, season, stage, MAX(imported_at) FROM raw_projections "
                       "WHERE player_id IS NOT NULL GROUP BY source, season, stage").fetchall()
    for source, season, stage, as_of in raw:
        rows = main.execute(
            f"SELECT r.player_id, p.name_key, COALESCE(p.canonical_name, r.player), r.team, r.positions, {cols} "
            "FROM raw_projections r LEFT JOIN players p ON p.player_id = r.player_id "
            "WHERE r.source=? AND r.season=? AND r.stage=? AND r.player_id IS NOT NULL "
            "ORDER BY r.player_id, r.team", (source, season, stage)).fetchall()
        out.append(("raw", source, season, stage, as_of, rows))
    has_final = main.execute("SELECT 1 FROM sqlite_master WHERE name='final_projections'").fetchone()
    if has_final:
        finals = main.execute("SELECT phase, season, MAX(computed_at) FROM final_projections "
                              "GROUP BY phase, season").fetchall()
        for phase, season, as_of in finals:
            rows = main.execute(
                f"SELECT r.player_id, p.name_key, r.player, r.team, r.positions, {cols} "
                "FROM final_projections r LEFT JOIN players p ON p.player_id = r.player_id "
                "WHERE r.phase=? AND r.season=? ORDER BY r.player_id", (phase, season)).fetchall()
            out.append(("final", phase, season, phase, as_of, rows))
    return out


def other_season(season, stage, as_of, rows):
    """Stats réelles d'une autre saison (ex. saison précédente lue avant le premier match) : plus de matchs
    joués que de jours écoulés depuis le 1er octobre de la saison."""
    if stage != "sea":
        return False
    try:
        start = datetime(int(str(season)[:4]), 10, 1).date()
    except ValueError:
        return False
    gp = max((r[5] or 0 for r in rows), default=0)   # r[5] = gp (FIELDS[0])
    return gp > max((_day(as_of) - start).days, 0)


def _hash(rows):
    """Empreinte du contenu (joueur + stats arrondies) : indépendante de la date d'import."""
    norm = [[r[1] or r[0], r[3] or ""] + [None if v is None else round(float(v), 4) for v in r[5:]] for r in rows]
    return hashlib.sha1(json.dumps(norm, separators=(",", ":")).encode()).hexdigest()


def take(main_path, history=None, league=None, now=None):
    """Photographie les données de la base main_path. Renvoie [(kind, source, stage, statut, n)]."""
    conf = _conf(league)
    now = now or datetime.now()
    report = []
    own = history is None
    hist = history or connect()
    try:
        with closing(sqlite3.connect(main_path)) as main:
            groups = _groups(main)
        for kind, source, season, stage, as_of, rows in groups:
            if not rows or not as_of:
                continue
            if kind == "raw" and other_season(season, stage, as_of, rows):
                report.append((kind, source, stage, "stats d'une autre saison, ignorée", len(rows)))
                continue
            digest = _hash(rows)
            last = hist.execute("SELECT as_of, content_hash FROM snapshots WHERE kind=? AND source=? AND season=? "
                                "AND stage=? ORDER BY as_of DESC, snapshot_id DESC LIMIT 1",
                                (kind, source, season, stage)).fetchone()
            same = hist.execute("SELECT 1 FROM snapshots WHERE kind=? AND source=? AND season=? AND stage=? "
                                "AND content_hash=?", (kind, source, season, stage, digest)).fetchone()
            if same:
                report.append((kind, source, stage, "inchangée", len(rows)))
                continue
            gap = interval_days(kind, stage, conf)
            if last and gap and (_day(as_of) - _day(last[0])).days < gap:
                report.append((kind, source, stage, f"moins de {gap} j depuis la précédente", len(rows)))
                continue
            with hist:
                cur = hist.execute("INSERT INTO snapshots (kind, source, season, stage, as_of, taken_at, "
                                   "content_hash, n_rows) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                   (kind, source, season, stage, str(as_of), now.isoformat(timespec="seconds"),
                                    digest, len(rows)))
                sid = cur.lastrowid
                marks = ", ".join("?" for _ in range(6 + len(FIELDS)))
                hist.executemany(f"INSERT INTO snapshot_rows (snapshot_id, player_id, name_key, player, team, "
                                 f"positions, {', '.join(FIELDS)}) VALUES ({marks})",
                                 [(sid,) + tuple(r) for r in rows])
            report.append((kind, source, stage, "photographiée", len(rows)))
        purge(hist, int(conf.get("retention_seasons", 3)))
    finally:
        if own:
            hist.close()
    return report


def purge(hist, keep):
    """Efface les photos des saisons au-delà des `keep` plus récentes."""
    seasons = [s for (s,) in hist.execute("SELECT DISTINCT season FROM snapshots ORDER BY season DESC")]
    old = seasons[max(keep, 1):]
    if old:
        with hist:
            marks = ", ".join("?" for _ in old)
            hist.execute(f"DELETE FROM snapshot_rows WHERE snapshot_id IN "
                         f"(SELECT snapshot_id FROM snapshots WHERE season IN ({marks}))", old)
            hist.execute(f"DELETE FROM snapshots WHERE season IN ({marks})", old)
        log.info("[History] Photos effacées pour : %s.", ", ".join(old))
    return old


def status(history=None):
    hist = history or connect()
    rows = hist.execute("SELECT kind, source, season, stage, COUNT(*), MIN(as_of), MAX(as_of), MAX(n_rows) "
                        "FROM snapshots GROUP BY kind, source, season, stage "
                        "ORDER BY season DESC, kind DESC, source, stage").fetchall()
    if history is None:
        hist.close()
    return rows


def fetch_online(main_path, step=None):
    """Importe les sources en ligne (étape ros + stats par période) dans une copie temporaire de la base
    principale, sans pondération ni envoi vers le classeur. Renvoie le chemin de la copie (ou None)."""
    tmp_dir = tempfile.mkdtemp(prefix="stbl_history_")
    tmp_path = os.path.join(tmp_dir, "database.sqlite")
    shutil.copyfile(main_path, tmp_path)
    from scripts.cloud import RUNNER_ENV

    # RUNNER_ENV : pas de synchronisation Drive pour cette copie (elle ne doit jamais remplacer la base Drive)
    env = dict(os.environ, STBL_DB_PATH=tmp_path, **{RUNNER_ENV: "1"})
    args = [sys.executable, "main.py", "--stage", "ros", "--skip-weighting", "--skip-yahoo", "--no-sync-config"]
    rc = (step or (lambda a, e: subprocess.call(a, cwd=BASE_DIR, env=e)))(args, env)
    if rc != 0:
        log.warning("[History] Import des sources en ligne incomplet (code %s) : photos des données reçues.", rc)
    return tmp_path


def _print(report):
    for kind, source, stage, state, n in report:
        log.info("[History] %-5s %-14s %-6s %4d joueurs : %s", kind, source, stage, n, state)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Photos des projections et stats réelles (history.sqlite)")
    parser.add_argument("action", choices=["snapshot", "daily", "status"])
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    from scripts import db

    try:
        league = load_league()
    except Exception:   # configuration absente : réglages par défaut
        league = {}
    if args.action == "status":
        for kind, source, season, stage, n, first, last, size in status():
            print(f"{season} {kind:<5} {source:<14} {stage:<6} {n:>3} photo(s), {first[:10]} -> {last[:10]}, "
                  f"{size} joueurs max")
        return 0
    if not os.path.exists(db.DB_PATH):
        log.error("Base %s absente.", db.DB_PATH)
        return 1
    with closing(connect()) as hist:
        if args.action == "daily":
            load_settings()   # contrôle de la configuration avant l'import
            tmp = fetch_online(db.DB_PATH)
            try:
                log.info("[History] Sources en ligne du jour :")
                _print(take(tmp, hist, league))
            finally:
                shutil.rmtree(os.path.dirname(tmp), ignore_errors=True)
        log.info("[History] Base principale :")
        _print(take(db.DB_PATH, hist, league))
    return 0


if __name__ == "__main__":
    sys.exit(main())
