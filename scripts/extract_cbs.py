import os
import sqlite3
import pandas as pd
from scraper_cbs import fetch_cbs_projections, clean_cbs_player_name, get_master_players_maps, smart_match_player

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "database.sqlite")


def run_full_cbs_extraction(positions=None):
    """Scrape toutes les positions demandées via la logique unifiée de scraper_cbs."""
    if positions is None:
        positions = ["PG", "SG", "SF", "PF", "C"]

    print("🚀 [CBS Extraction] Démarrage du scraping complet...")
    success_count = 0
    for pos in positions:
        if fetch_cbs_projections(position=pos):
            success_count += 1

    print(f"🏁 [CBS Extraction] Terminé : {success_count}/{len(positions)} positions scrapées avec succès.")


def reprocess_cbs_database():
    """Consulte la table SQLite et ré-harmonise les IDs pour les entrées CBS déjà présentes."""
    if not os.path.exists(DB_PATH):
        print("❌ [CBS Re-process] Base de données introuvable.")
        return

    conn = sqlite3.connect(DB_PATH)
    full_map, initial_map = get_master_players_maps(conn)

    df = pd.read_sql_query("SELECT * FROM raw_projections WHERE source='CBS'", conn)

    if df.empty:
        print("ℹ️ [CBS Re-process] Aucune donnée CBS trouvée dans 'raw_projections'.")
        conn.close()
        return

    df["player"] = df["player"].apply(clean_cbs_player_name)
    df["master_player_id"] = df["player"].apply(
        lambda name: smart_match_player(name, full_map, initial_map)
    )

    cursor = conn.cursor()
    cursor.execute("DELETE FROM raw_projections WHERE source='CBS'")
    df.to_sql("raw_projections", conn, if_exists="append", index=False)
    conn.commit()
    conn.close()

    matched = df["master_player_id"].notna().sum()
    print(f"✅ [CBS Re-process] {matched}/{len(df)} joueurs CBS correctement reliés à master_players.")


if __name__ == "__main__":
    # Exécute l'extraction complète sur les 5 postes
    run_full_cbs_extraction()