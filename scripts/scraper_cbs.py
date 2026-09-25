import json
import os
import re
import sqlite3
from io import StringIO
import cloudscraper
import pandas as pd
from bs4 import BeautifulSoup

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "database.sqlite")
SETTINGS_PATH = os.path.join(BASE_DIR, "config", "settings.json")
MAPPINGS_PATH = os.path.join(BASE_DIR, "config", "mappings.json")

def load_settings():
    if os.path.exists(SETTINGS_PATH):
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"active_season": "2026-27", "active_stage": "draft"}

def load_mapping(source_name):
    if os.path.exists(MAPPINGS_PATH):
        with open(MAPPINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get(source_name, {})
    return {}

def clean_cbs_player_name(raw_name):
    if not raw_name or pd.isna(raw_name):
        return ""
    text = str(raw_name).strip()
    text = re.sub(r"\b(DTD|GTD|OUT|INJ|SUSP)\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(PG|SG|SF|PF|C|G|F)\s+[A-Z]{2,3}\b.*$", "", text, flags=re.IGNORECASE)
    return text.split("  ")[0].strip()

def fetch_cbs_projections(position="PG"):
    settings = load_settings()
    season, stage = settings.get("active_season"), settings.get("active_stage")
    year = season.split("-")[0]

    cbs_config = load_mapping("cbs")
    template_url = cbs_config.get("urls", {}).get(stage)
    if not template_url:
        print(f"❌ [CBS] URL non trouvée pour le stage '{stage}'.")
        return False

    url = template_url.format(position=position.upper(), year=year)
    print(f"📥 [CBS] Scraping {position}...")

    scraper = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "desktop": True})
    response = scraper.get(url)

    if response.status_code != 200:
        print(f"❌ [CBS] Erreur HTTP {response.status_code}")
        return False

    soup = BeautifulSoup(response.text, "lxml")
    table = soup.find("table")
    if not table:
        print(f"❌ [CBS] Aucun tableau trouvé pour {position}.")
        return False

    df = pd.read_html(StringIO(str(table)))[0]
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(0)

    df.columns = [str(col).lower().strip() for col in df.columns]
    cols_map = {str(k).lower().strip(): v for k, v in cbs_config.get("columns", {}).items()}
    df = df.rename(columns=cols_map)

    if "player" in df.columns:
        df["player"] = df["player"].apply(clean_cbs_player_name)

    df["source"] = "CBS"
    df["season"] = season
    df["stage"] = stage
    df["position"] = position.upper()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM raw_projections WHERE source='CBS' AND position=? AND season=? AND stage=?",
        (position.upper(), season, stage),
    )

    valid_cols = [col for col in df.columns if col in [
        "player", "position", "source", "season", "stage", "gp", "min", "pts",
        "reb", "ast", "stl", "blk", "to", "fgm", "fga", "fgp", "ftm", "fta", "ftp", "3pm"
    ]]
    df_to_save = df[valid_cols].drop_duplicates(subset=["player"], keep="first")
    df_to_save.to_sql("raw_projections", conn, if_exists="append", index=False)

    conn.commit()
    conn.close()
    print(f"✅ [CBS] {len(df_to_save)} joueurs insérés pour {position}.")
    return True

if __name__ == "__main__":
    for pos in ["PG", "SG", "SF", "PF", "C"]:
        fetch_cbs_projections(pos)