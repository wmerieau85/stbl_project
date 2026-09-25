import json
import os
from scripts.db import init_db
from scripts.scraper_cbs import fetch_cbs_projections
from scripts.scraper_fantasypros import fetch_fantasypros_projections

SETTINGS_PATH = os.path.join("config", "settings.json")


def load_settings():
    if os.path.exists(SETTINGS_PATH):
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"sources": {"cbs": True, "fantasypros": True}}


def run_pipeline():
    settings = load_settings()
    sources_config = settings.get("sources", {})

    print("=== DÉMARRAGE DU PIPELINE STBL ===")
    init_db()

    positions = ["PG", "SG", "SF", "PF", "C"]

    # CBS
    if sources_config.get("cbs", False):
        print("\n--- Lancement du scraping CBS ---")
        for pos in positions:
            fetch_cbs_projections(position=pos)

    # FantasyPros
    if sources_config.get("fantasypros", False):
        print("\n--- Lancement du scraping FantasyPros ---")
        fetch_fantasypros_projections(position="overall")

    print("\n✅ Pipeline terminé !")


if __name__ == "__main__":
    run_pipeline()