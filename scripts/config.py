"""Chemins du projet et chargement de la configuration (source unique)."""

import copy
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
DB_PATH = os.path.join(BASE_DIR, "database.sqlite")
# Configuration : l'onglet « config » du classeur fait foi. config/config.json en est la copie locale
# (écrite par config_sheet.pull, ignorée par git) ; config/defaults.json sert tant qu'elle n'existe pas.
# config/bootstrap.json : ce qu'il faut connaître avant de lire le classeur (ID, onglet, identifiants).
BOOTSTRAP_PATH = os.path.join(CONFIG_DIR, "bootstrap.json")
DEFAULTS_PATH = os.path.join(CONFIG_DIR, "defaults.json")
CONFIG_PATH = os.environ.get("STBL_CONFIG") or os.path.join(CONFIG_DIR, "config.json")
# libellés des catégories dans l'onglet config (Settings | Scoring)
CATEGORY_LABELS_SHEET = {"fgp": "FG%", "fg3m": "3PM", "ftp": "FT%", "reb": "REB", "ast": "AST", "stl": "STL",
                         "blk": "BLK", "tov": "TO", "pts": "PTS"}
EXPORTS_DIR = os.path.join(BASE_DIR, "exports")

# Valeurs de secours si une clé manque dans la configuration.
DEFAULT_SETTINGS = {
    "active_season": "2026-27",
    "active_stage": "draft",  # "draft" ou "ros"
    # stats réelles par période, importées avec l'étape ros (codes fp.sea, fp.l30... des grilles)
    "stats_windows": {"fantasypros": ["sea", "l30", "l15", "l07"]},
    # sources activées par étape : {"cbs": {"draft": True, "ros": True}, ...}
    "sources": {name: {"draft": True, "ros": True}
                for name in ("cbs", "fantasypros", "fanscout", "draftkick", "lineupexperts")},
    "google": {"service_account_file": "credentials/service_account.json"},
    "yahoo": {"app_file": "credentials/yahoo_app.json", "token_file": "credentials/yahoo_token.json",
              "redirect_uri": "https://localhost:8080"},
    "http": {"timeout": 30, "retries": 2, "pause_seconds": 1.5},
    # Phases de pondération calculées selon l'étape active (grilles de l'onglet config)
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


def load_bootstrap():
    data = {"spreadsheet_id": "", "config_tab": "config",
            "google": {"service_account_file": "credentials/service_account.json"},
            "yahoo": {"app_file": "credentials/yahoo_app.json", "token_file": "credentials/yahoo_token.json",
                      "redirect_uri": "https://localhost:8080"}}
    if os.path.exists(BOOTSTRAP_PATH):
        data = _deep_merge(data, _read_json(BOOTSTRAP_PATH))
    return data


def save_bootstrap(data):
    _write_json(BOOTSTRAP_PATH, data)


def load_config():
    """Configuration complète {league, settings, sources, grids, aliases} : copie locale de l'onglet
    config (config/config.json), sinon valeurs livrées (config/defaults.json)."""
    path = CONFIG_PATH if os.path.exists(CONFIG_PATH) else DEFAULTS_PATH
    data = _read_json(path) if os.path.exists(path) else {}
    for key, empty in (("league", {}), ("settings", {}), ("sources", {}), ("grids", {}), ("aliases", {})):
        data.setdefault(key, empty)
    return data


def save_config(data):
    _write_json(CONFIG_PATH, data)


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _stage_flags(value):
    if isinstance(value, dict):
        return {"draft": bool(value.get("draft", False)), "ros": bool(value.get("ros", False))}
    return {"draft": bool(value), "ros": bool(value)}


def load_settings():
    """Réglages actifs : valeurs par défaut + configuration + chemins locaux (bootstrap)."""
    settings = _deep_merge(DEFAULT_SETTINGS, load_config()["settings"])
    boot = load_bootstrap()
    settings["google"] = boot["google"]
    settings["yahoo"] = boot["yahoo"]
    settings["sources"] = {name: _stage_flags(v) for name, v in settings["sources"].items()}
    return settings


def enabled_sources(settings, stage=None):
    stage = stage or settings["active_stage"]
    return [name for name, flags in settings["sources"].items() if _stage_flags(flags).get(stage)]


def load_source_config(source_name):
    """Réglages propres à une source (URL / fichiers, colonnes...)."""
    sources = load_sources_config()
    if source_name not in sources:
        raise KeyError(f"Source '{source_name}' absente de la configuration (onglet config, sections Sources)")
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
    """Paramètres de la ligue complétés par les valeurs par défaut et le bootstrap (ID du classeur)."""
    league = load_config()["league"]
    merged = _deep_merge(DEFAULT_LEAGUE, league)
    # roster et catégories : la liste de la configuration remplace entièrement celle par défaut
    for key in ("roster", "categories"):
        if key in league:
            merged[key] = league[key]
    boot = load_bootstrap()
    merged["google_sheets"]["draft_spreadsheet_id"] = boot.get("spreadsheet_id", "")
    merged["google_sheets"]["config_tab"] = boot.get("config_tab") or "config"
    return merged


def load_sources_config():
    return load_config()["sources"]


def load_aliases():
    """Alias de noms : {"nom vu dans une source": "nom canonique"} (onglet config_alias)."""
    return load_config()["aliases"]


def load_grid_rows(phase):
    """Grille de pondération d'une phase, sous forme de lignes (disposition CSV historique)."""
    return load_config()["grids"].get(phase)


def starting_slots(league):
    """Nombre de postes titulaires (hors banc / IL) : 2 G + 2 F + 1 C + 3 UTIL = 8."""
    return sum(int(n) for pos, n in league["roster"].items() if pos.upper() not in NON_STARTING_SLOTS)


def games_cap(league):
    """Plafond de matchs comptabilisés par équipe sur la saison : 82 x 8 = 656."""
    return int(league.get("games", {}).get("per_slot", 82)) * starting_slots(league)
