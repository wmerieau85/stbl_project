"""Accès SQLite : connexion, schéma et migrations."""

import logging
import sqlite3
from contextlib import contextmanager

from scripts.config import DB_PATH

log = logging.getLogger(__name__)

SCHEMA_VERSION = 3

# Colonnes de stats standardisées (totaux sur la saison, pourcentages en décimal 0-1)
STAT_COLUMNS = [
    "gp", "gs", "min", "mpg",
    "pts", "reb", "ast", "stl", "blk", "tov",
    "fgm", "fga", "fgp",
    "ftm", "fta", "ftp",
    "fg3m", "fg3a", "fg3p",
    "fpts",
]
PERCENT_COLUMNS = {"fgp", "ftp", "fg3p"}

# Informations de draft (hors stats) : ADP et coût moyen en enchères
META_COLUMNS = ["adp", "auction_cost"]
NUMERIC_COLUMNS = STAT_COLUMNS + META_COLUMNS

PROJECTION_COLUMNS = [
    "player", "player_key", "team", "positions", "source", "season", "stage",
] + NUMERIC_COLUMNS


@contextmanager
def get_connection():
    """Connexion fermée automatiquement, même en cas d'erreur."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def _migrate(conn, current_version):
    if current_version == 2:
        # v2 -> v3 : ajout des colonnes de draft, sans perte de données
        log.info("Migration du schéma v2 -> v3 (colonnes %s).", ", ".join(META_COLUMNS))
        for col in META_COLUMNS:
            conn.execute(f"ALTER TABLE raw_projections ADD COLUMN {col} REAL")
        conn.execute("DROP VIEW IF EXISTS v_projections")
        return
    if 0 < current_version < 2 or (
        current_version == 0 and _table_exists(conn, "raw_projections")
    ):
        # Ancien schéma (colonnes "to"/"3pm", pas de player_key) : les données brutes
        # sont ré-importables et la table players était générée automatiquement.
        log.warning("Ancien schéma détecté : recréation de raw_projections et players.")
        conn.execute("DROP VIEW IF EXISTS v_projections")
        conn.execute("DROP TABLE IF EXISTS raw_projections")
        conn.execute("DROP TABLE IF EXISTS players")


def _table_exists(conn, name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def init_db():
    stats_sql = ",\n            ".join(f"{col} REAL" for col in NUMERIC_COLUMNS)
    with get_connection() as conn, conn:
        current_version = conn.execute("PRAGMA user_version").fetchone()[0]
        if current_version != SCHEMA_VERSION:
            _migrate(conn, current_version)

        # Référentiel joueurs : un joueur = une clé de rapprochement unique
        conn.execute("""
            CREATE TABLE IF NOT EXISTS players (
                player_id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_name TEXT NOT NULL,
                name_key TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Projections brutes : une ligne par joueur, source, saison et étape
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS raw_projections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER REFERENCES players (player_id),
                player TEXT NOT NULL,
                player_key TEXT NOT NULL,
                team TEXT NOT NULL DEFAULT '',
                positions TEXT,
                source TEXT NOT NULL,
                season TEXT NOT NULL,
                stage TEXT NOT NULL,
                {stats_sql},
                imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (source, season, stage, player_key, team)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_raw_player_key ON raw_projections (player_key)"
        )

        # Vue prête pour l'export (Google Sheets) : nom canonique + projections
        conn.execute("""
            CREATE VIEW IF NOT EXISTS v_projections AS
            SELECT p.player_id, p.canonical_name, r.*
            FROM raw_projections r
            LEFT JOIN players p ON p.player_id = r.player_id
        """)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    log.info("Base de données prête (%s).", DB_PATH)


def replace_projections(records, source, season, stage):
    """Remplace, en une seule transaction, les projections d'une source/saison/étape."""
    placeholders = ", ".join("?" for _ in PROJECTION_COLUMNS)
    columns_sql = ", ".join(PROJECTION_COLUMNS)
    rows = [tuple(rec.get(col) for col in PROJECTION_COLUMNS) for rec in records]
    with get_connection() as conn, conn:
        conn.execute(
            "DELETE FROM raw_projections WHERE source=? AND season=? AND stage=?",
            (source, season, stage),
        )
        conn.executemany(
            f"INSERT INTO raw_projections ({columns_sql}) VALUES ({placeholders})", rows
        )
    return len(rows)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    init_db()
