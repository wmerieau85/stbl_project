import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "database.sqlite")

def get_connection():
    return sqlite3.connect(DB_PATH)

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # Table Référentiel Joueurs
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS players (
            player_id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_name TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Table des projections brutes
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS raw_projections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER,
            player TEXT NOT NULL,
            position TEXT,
            source TEXT NOT NULL,
            season TEXT NOT NULL,
            stage TEXT NOT NULL,
            gp REAL,
            min REAL,
            pts REAL,
            reb REAL,
            ast REAL,
            stl REAL,
            blk REAL,
            "to" REAL,
            fgm REAL,
            fga REAL,
            fgp REAL,
            ftm REAL,
            fta REAL,
            ftp REAL,
            "3pm" REAL,
            imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (player_id) REFERENCES players (player_id)
        )
    """)

    conn.commit()
    conn.close()
    print("✅ [DB] Base de données initialisée proprement.")

if __name__ == "__main__":
    init_db()