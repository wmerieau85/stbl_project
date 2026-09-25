import json
import os
import re
import sqlite3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "database.sqlite")
ALIASES_PATH = os.path.join(BASE_DIR, "config", "player_aliases.json")

def load_aliases():
    if os.path.exists(ALIASES_PATH):
        with open(ALIASES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def clean_name(name: str) -> str:
    if not name:
        return ""
    s = re.sub(r"\b(DTD|GTD|OUT|INJ|SUSP)\b", "", str(name), flags=re.IGNORECASE)
    s = re.sub(r"\b(Jr\.?|Sr\.?|III|II|IV)\b", "", s, flags=re.IGNORECASE)
    s = s.replace(".", "").replace("'", "")
    return " ".join(s.split()).strip()

def link_players():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    aliases = load_aliases()

    # Récupérer les noms non liés
    cursor.execute("SELECT DISTINCT player FROM raw_projections WHERE player_id IS NULL")
    raw_names = [row[0] for row in cursor.fetchall() if row[0]]

    print(f"🔗 [Linker] Association de {len(raw_names)} joueurs...")

    for raw_name in raw_names:
        target_name = aliases.get(raw_name, raw_name)
        cleaned = clean_name(target_name)

        # Vérification si le joueur existe déjà dans `players`
        cursor.execute("SELECT player_id FROM players WHERE LOWER(canonical_name) = LOWER(?)", (cleaned,))
        row = cursor.fetchone()

        if row:
            player_id = row[0]
        else:
            cursor.execute("INSERT INTO players (canonical_name) VALUES (?)", (cleaned,))
            player_id = cursor.lastrowid
            print(f"  🆕 Nouveau joueur créé : '{cleaned}' (ID: {player_id})")

        cursor.execute("UPDATE raw_projections SET player_id = ? WHERE player = ?", (player_id, raw_name))

    conn.commit()
    conn.close()
    print("✅ [Linker] Harmonisation terminée !")

if __name__ == "__main__":
    link_players()