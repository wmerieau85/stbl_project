import json
import os
import re
import sqlite3
from io import StringIO
import cloudscraper
import pandas as pd
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"}
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "database.sqlite")
SETTINGS_PATH = os.path.join(BASE_DIR, "config", "settings.json")
MAPPINGS_PATH = os.path.join(BASE_DIR, "config", "mappings.json")

def load_settings():
    if os.path.exists(SETTINGS_PATH):
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"active_season": "2026-27", "active_stage": "draft"}

def fetch_fantasypros_projections(position="overall"):
    settings = load_settings()
    season, stage = settings.get("active_season"), settings.get("active_stage")

    with open(MAPPINGS_PATH, "r", encoding="utf-8") as f:
        fp_config = json.load(f).get("fantasypros", {})

    base_url = fp_config.get("urls", {}).get(stage)
    pos_url = "" if position.lower() == "overall" else f"?position={position}"
    url = f"{base_url}{pos_url}"

    print(f"📥 [FantasyPros] Scraping {position}...")
    scraper = cloudscraper.create_scraper()
    response = scraper.get(url, headers=HEADERS)

    if response.status_code != 200:
        return False

    soup = BeautifulSoup(response.text, "lxml")
    table = soup.find("table", {"id": "data"})
    if not table:
        return False

    df = pd.read_html(StringIO(str(table)))[0]
    df = df.rename(columns=fp_config.get("columns", {}))

    if "player" in df.columns:
        df["player"] = df["player"].apply(
            lambda x: re.sub(r"\s*\([^)]*\)|\b(DTD|GTD|OUT|INJ|SUSP)\b", "", str(x), flags=re.IGNORECASE).strip()
        )

    df["source"] = "FantasyPros"
    df["season"] = season
    df["stage"] = stage
    df["position"] = position.upper()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM raw_projections WHERE source='FantasyPros' AND position=? AND season=? AND stage=?",
        (position.upper(), season, stage),
    )

    valid_cols = [col for col in df.columns if col in [
        "player", "position", "source", "season", "stage", "gp", "min", "pts",
        "reb", "ast", "stl", "blk", "to", "fgp", "ftp", "3pm"
    ]]
    df[valid_cols].to_sql("raw_projections", conn, if_exists="append", index=False)
    conn.commit()
    conn.close()

    print(f"✅ [FantasyPros] Données insérées pour {position}.")
    return True

if __name__ == "__main__":
    fetch_fantasypros_projections("overall")