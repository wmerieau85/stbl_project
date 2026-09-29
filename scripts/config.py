"""Chemins du projet et chargement de la configuration (source unique)."""

import copy
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
DB_PATH = os.path.join(BASE_DIR, "database.sqlite")
SETTINGS_PATH = os.path.join(CONFIG_DIR, "settings.json")
SOURCES_PATH = os.path.join(CONFIG_DIR, "sources.json")
ALIASES_PATH = os.path.join(CONFIG_DIR, "player_aliases.json")
LEAGUE_PATH = os.path.join(CONFIG_DIR, "league.json")
WEIGHTS_DIR = os.path.join(CONFIG_DIR, "weights")
EXPORTS_DIR = os.path.join(BASE_DIR, "exports")

# Valeurs de secours si une clé manque dans config/settings.json.
DEFAULT_SETTINGS = {
    "active_season": "2026-27",
    "active_stage": "draft",  # "draft" ou "ros"
    # stats réelles par période, importées avec l'étape ros (codes fp.sea, fp.l30... des grilles)
    "stats_windows": {"fantasypros": ["sea", "l30", "l15", "l07"]},
    "sources": {"cbs": True, "fantasypros": True, "fanscout": True, "draftkick": True, "lineupexperts": True},
    "google": {"service_account_file": "credentials/service_account.json"},
    "yahoo": {"app_file": "credentials/yahoo_app.json", "token_file": "credentials/yahoo_token.json",
              "redirect_uri": "https://localhost:8080"},
    "http": {"timeout": 30, "retries": 2, "pause_seconds": 1.5},
    # Phases de pondération calculées selon l'étape active (grilles dans config/weights/)
    "phases": {"draft": ["draft"], "ros": ["lt", "st"]},
    # Export CSV des projections finales (format Excel / Google Sheets FR par défaut)
    "export": {"delimiter": ";", "decimal": ","},
}


def _read_json(path):
    # utf-8-sig : tolère un BOM éventuel (fichiers édités sous Windows)
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def _deep_merge(base, override):
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_settings():
    """Retourne les réglages actifs (valeurs par défaut + config/settings.json)."""
    if os.path.exists(SETTINGS_PATH):
        return _deep_merge(DEFAULT_SETTINGS, _read_json(SETTINGS_PATH))
    return copy.deepcopy(DEFAULT_SETTINGS)


def load_source_config(source_name):
    """Retourne le bloc de config/sources.json propre à une source."""
    sources = _read_json(SOURCES_PATH)
    if source_name not in sources:
        raise KeyError(f"Source '{source_name}' absente de config/sources.json")
    return sources[source_name]


DEFAULT_LEAGUE = {
    "format": "h2h",
    "teams": 12,
    "roster": {"PG": 1, "SG": 1, "G": 1, "SF": 1, "PF": 1, "F": 1, "C": 2, "UTIL": 2, "BN": 3},
    "categories": {"fgp": 1, "fg3m": 1, "ftp": 1, "reb": 1, "ast": 1, "stl": 1, "blk": 1, "tov": 1, "pts": 1},
    "zscore": {"min_gp": 0, "iterations": 3},
    "platform": "yahoo",
    "yahoo": {"league_id": ""},
    "season": {"st_days": 15, "lt_phase": "lt", "st_phase": "st", "simulations": 2000,
               "roster_source": "yahoo", "roster_tab": "rosters"},
    "games": {"per_slot": 82, "lineup": "daily"},
    "draft": {
        "type": "snake", "rounds": 12, "my_team": "", "order": [], "keepers": {}, "keeper_rounds": [], "picks_source": "sheet",
        "candidates": 40, "simulations": 40, "adp_noise": 0.15,
    },
    "google_sheets": {
        "draft_spreadsheet_id": "", "config_tab": "config", "picks_tab": "draft_res", "picks_range": "A2:D",
        "reco_tab": "draft_reco", "projections_spreadsheet_id": "", "projections_tab": "proj",
        "projections_start_col": "A", "poll_seconds": 10, "write_picks_to_sheet": True, "season_tab": "season",
        "alias_tab": "config_alias",
    },
}

# Postes qui ne comptent pas dans le plafond de matchs (banc, blessés)
NON_STARTING_SLOTS = ("BN", "IL", "IL+")


def load_league():
    """Paramètres de la ligue (config/league.json) complétés par les valeurs par défaut."""
    if not os.path.exists(LEAGUE_PATH):
        return copy.deepcopy(DEFAULT_LEAGUE)
    league = _read_json(LEAGUE_PATH)
    merged = _deep_merge(DEFAULT_LEAGUE, league)
    # roster et catégories : la liste du fichier remplace entièrement celle par défaut
    for key in ("roster", "categories"):
        if key in league:
            merged[key] = league[key]
    return merged


def load_sources_config():
    return _read_json(SOURCES_PATH)


def load_aliases():
    """Alias de noms : {"nom vu dans une source": "nom canonique"}."""
    if os.path.exists(ALIASES_PATH):
        return _read_json(ALIASES_PATH)
    return {}


def starting_slots(league):
    """Nombre de postes titulaires (hors banc / IL) : 2 G + 2 F + 1 C + 3 UTIL = 8."""
    return sum(int(n) for pos, n in league["roster"].items() if pos.upper() not in NON_STARTING_SLOTS)


def games_cap(league):
    """Plafond de matchs comptabilisés par équipe sur la saison : 82 x 8 = 656."""
    return int(league.get("games", {}).get("per_slot", 82)) * starting_slots(league)
