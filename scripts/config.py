"""Chemins du projet et chargement de la configuration (source unique)."""

import copy
import json
import math
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
# libellés des catégories dans l'onglet settings (Settings | Scoring)
CATEGORY_LABELS_SHEET = {"fgp": "FG%", "fg3m": "3PM", "ftp": "FT%", "reb": "REB", "ast": "AST", "stl": "STL",
                         "blk": "BLK", "tov": "TO", "pts": "PTS"}
EXPORTS_DIR = os.path.join(BASE_DIR, "exports")

# Valeurs de secours si une clé manque dans la configuration.
DEFAULT_SETTINGS = {
    "active_season": "2026-27",
    "active_stage": "draft",  # "draft" ou "ros"
    # stats réelles par période, importées avec l'étape ros (codes fp.sea, fp.l30... des grilles)
    "stats_windows": {"fantasypros": ["sea", "l30", "l15", "l07"], "ninecat": ["sea"]},
    # sources activées par étape : {"cbs": {"draft": True, "ros": True}, ...}
    "sources": dict({name: {"draft": True, "ros": True}
                     for name in ("cbs", "fantasypros", "fanscout", "draftkick", "lineupexperts", "ninecat")},
                    espn={"draft": True, "ros": False}, rotoballer={"draft": True, "ros": False},
                    fantasynerds={"draft": False, "ros": False}),   # API payante : clé nécessaire
    "google": {"service_account_file": "credentials/service_account.json"},
    "yahoo": {"app_file": "credentials/yahoo_app.json", "token_file": "credentials/yahoo_token.json",
              "redirect_uri": "https://localhost:8080"},
    "http": {"timeout": 30, "retries": 2, "pause_seconds": 1.5},
    # Phases de pondération calculées selon l'étape active (grilles de l'onglet settings)
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


def _validate_mapping(value, label, *, allow_empty=False):
    if not isinstance(value, dict):
        raise ValueError(f"{label} doit être un dictionnaire.")
    if not allow_empty and not value:
        raise ValueError(f"{label} ne peut pas être vide.")
    return value


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _normalize_source_flags(sources):
    normalized = {}
    for name, flags in sources.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("settings.sources contient un nom de source invalide.")
        label = f"settings.sources['{name}']"
        if isinstance(flags, bool):
            normalized[name] = _stage_flags(flags)
            continue
        stage_map = _validate_mapping(flags, label, allow_empty=True)
        for stage, value in stage_map.items():
            if stage not in {"draft", "ros"}:
                raise ValueError(f"{label} contient un stage inconnu : '{stage}'.")
            if not isinstance(value, bool):
                raise ValueError(f"{label}['{stage}'] doit être un booléen.")
        normalized[name] = _stage_flags(stage_map)
    return normalized


def validate_settings(settings):
    """Vérifie les réglages actifs de l'application."""
    settings = _validate_mapping(settings, "settings")
    active_season = settings.get("active_season")
    if not isinstance(active_season, str) or not active_season.strip():
        raise ValueError("settings.active_season doit être une chaîne non vide.")
    active_stage = settings.get("active_stage")
    if active_stage not in {"draft", "ros"}:
        raise ValueError("settings.active_stage doit être 'draft' ou 'ros'.")

    if "sources" in settings:
        _validate_mapping(settings["sources"], "settings.sources", allow_empty=True)

    if "phases" in settings:
        phases = _validate_mapping(settings["phases"], "settings.phases")
        for stage, phase_list in phases.items():
            if stage not in {"draft", "ros"}:
                raise ValueError(f"settings.phases['{stage}'] est invalide : stage inconnu.")
            if not isinstance(phase_list, list) or not phase_list:
                raise ValueError(f"settings.phases['{stage}'] doit être une liste non vide.")
            if not all(isinstance(item, str) and item.strip() for item in phase_list):
                raise ValueError(f"settings.phases['{stage}'] doit contenir uniquement des chaînes non vides.")

    if "export" in settings:
        export_cfg = _validate_mapping(settings["export"], "settings.export")
        delimiter = export_cfg.get("delimiter")
        decimal = export_cfg.get("decimal")
        if not isinstance(delimiter, str) or len(delimiter) != 1:
            raise ValueError("settings.export.delimiter doit être un unique caractère.")
        if not isinstance(decimal, str) or len(decimal) != 1:
            raise ValueError("settings.export.decimal doit être un unique caractère.")

    if "http" in settings:
        http_cfg = _validate_mapping(settings["http"], "settings.http")
        for key in ("timeout", "retries", "pause_seconds"):
            if key not in http_cfg:
                continue
            value = http_cfg[key]
            if key == "retries":
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise ValueError("settings.http.retries doit être un entier positif ou nul.")
            elif key == "timeout":
                if not _is_number(value) or value <= 0:
                    raise ValueError("settings.http.timeout doit être un nombre strictement positif.")
            elif not _is_number(value) or value < 0:
                raise ValueError("settings.http.pause_seconds doit être un nombre positif ou nul.")

    if "stats_windows" in settings:
        stats_windows = _validate_mapping(settings["stats_windows"], "settings.stats_windows")
        for source_name, windows in stats_windows.items():
            if not isinstance(source_name, str) or not source_name.strip():
                raise ValueError("settings.stats_windows contient un nom de source invalide.")
            if not isinstance(windows, list) or not windows:
                raise ValueError(f"settings.stats_windows['{source_name}'] doit être une liste non vide.")
            if not all(isinstance(item, str) and item.strip() for item in windows):
                raise ValueError(f"settings.stats_windows['{source_name}'] doit contenir uniquement des noms valides.")

    return settings


def validate_sources_config(sources):
    """Vérifie la configuration des sources importées."""
    sources_cfg = _validate_mapping(sources, "sources", allow_empty=True)
    for name, conf in sources_cfg.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("sources contient un nom de source invalide.")
        conf_map = _validate_mapping(conf, f"sources['{name}']", allow_empty=True)
        if "code" in conf_map and (not isinstance(conf_map["code"], str) or not conf_map["code"].strip()):
            raise ValueError(f"sources['{name}'].code doit être une chaîne non vide.")
        for key in ("urls", "files"):
            if key in conf_map:
                entries = _validate_mapping(conf_map[key], f"sources['{name}'].{key}", allow_empty=True)
                for entry, value in entries.items():
                    if not isinstance(entry, str) or not entry.strip():
                        raise ValueError(f"sources['{name}'].{key} contient une clé invalide.")
                    if not isinstance(value, str) or not value.strip():
                        raise ValueError(f"sources['{name}'].{key}[{entry!r}] doit être une chaîne non vide.")
        if "columns" in conf_map:
            columns = _validate_mapping(conf_map["columns"], f"sources['{name}'].columns", allow_empty=True)
            for key, value in columns.items():
                if not isinstance(key, str) or not key.strip():
                    raise ValueError(f"sources['{name}'].columns contient une clé invalide.")
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"sources['{name}'].columns[{key!r}] doit être une chaîne non vide.")
        if "positions" in conf_map:
            positions = conf_map["positions"]
            if not isinstance(positions, list) or not positions:
                raise ValueError(f"sources['{name}'].positions doit être une liste non vide.")
            if not all(isinstance(item, str) and item.strip() for item in positions):
                raise ValueError(f"sources['{name}'].positions doit contenir uniquement des chaînes valides.")
        if "percent_scale" in conf_map:
            scale = conf_map["percent_scale"]
            if not _is_number(scale) or scale <= 0:
                raise ValueError(f"sources['{name}'].percent_scale doit être un nombre strictement positif.")
    return sources_cfg


def _column_number(value):
    if not isinstance(value, str):
        raise ValueError("Colonne Google Sheets invalide : une chaîne de lettres attendue.")
    letters = value.strip().upper()
    if not letters or not letters.isalpha():
        raise ValueError(f"Colonne Google Sheets invalide : {value!r}.")
    index = 0
    for char in letters:
        if not ("A" <= char <= "Z"):
            raise ValueError(f"Colonne Google Sheets invalide : {value!r}.")
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index


def validate_league(league):
    """Vérifie la configuration de la ligue."""
    league_map = _validate_mapping(league, "league")
    if "teams" in league_map and (
        not isinstance(league_map["teams"], int) or isinstance(league_map["teams"], bool) or league_map["teams"] <= 0
    ):
        raise ValueError("league.teams doit être un entier strictement positif.")
    if "roster" in league_map:
        roster = _validate_mapping(league_map["roster"], "league.roster")
        for pos, count in roster.items():
            if not isinstance(pos, str) or not pos.strip():
                raise ValueError("league.roster contient un poste invalide.")
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise ValueError(f"league.roster['{pos}'] doit être un entier positif ou nul.")
    if "categories" in league_map:
        categories = _validate_mapping(league_map["categories"], "league.categories")
        for cat, weight in categories.items():
            if not isinstance(cat, str) or not cat.strip():
                raise ValueError("league.categories contient une clé invalide.")
            if not _is_number(weight) or weight < 0:
                raise ValueError(f"league.categories['{cat}'] doit être un nombre positif.")
    if "google_sheets" in league_map:
        gs = _validate_mapping(league_map["google_sheets"], "league.google_sheets", allow_empty=True)
        start_col = gs.get("projections_start_col", "C")
        if not isinstance(start_col, str) or not start_col.strip():
            raise ValueError("league.google_sheets.projections_start_col doit être une chaîne non vide.")
        start_col = start_col.strip()
        if _column_number(start_col) < 3:
            raise ValueError(
                "league.google_sheets.projections_start_col doit être une colonne à partir de C; "
                "A et B sont réservés aux formules de l'onglet bdd."
            )
    return league_map


def _validate_config_structure(data):
    if not isinstance(data, dict):
        raise ValueError("La configuration doit être un objet JSON.")
    for key in ("league", "settings", "sources", "grids", "aliases"):
        if key in data and not isinstance(data[key], dict):
            raise ValueError(f"{key} doit être un dictionnaire.")
    return data


def _effective_settings(config, bootstrap=None):
    settings = _deep_merge(DEFAULT_SETTINGS, config.get("settings", {}))
    settings["sources"] = _normalize_source_flags(settings["sources"])
    boot = bootstrap or load_bootstrap()
    settings["google"] = boot["google"]
    settings["yahoo"] = boot["yahoo"]
    return validate_settings(settings)


def _effective_league(config, bootstrap=None):
    supplied = config.get("league", {})
    if "google_sheets" in supplied:
        _validate_mapping(supplied["google_sheets"], "league.google_sheets", allow_empty=True)
    league = _deep_merge(DEFAULT_LEAGUE, supplied)
    for key in ("roster", "categories"):
        if key in supplied:
            league[key] = supplied[key]
    boot = bootstrap or load_bootstrap()
    league["google_sheets"]["draft_spreadsheet_id"] = boot.get("spreadsheet_id", "")
    league["google_sheets"]["config_tab"] = boot.get("config_tab") or "settings"
    return validate_league(league)


def validate_runtime_config(data, bootstrap=None):
    """Valide les valeurs effectives fusionnées avec les valeurs par défaut."""
    _validate_config_structure(data)
    _effective_settings(data, bootstrap)
    _effective_league(data, bootstrap)
    validate_sources_config(data.get("sources", {}))
    for key in ("grids", "aliases"):
        _validate_mapping(data.get(key, {}), key, allow_empty=True)
    return data


def load_bootstrap():
    data = {"spreadsheet_id": "", "config_tab": "settings",
            "google": {"service_account_file": "credentials/service_account.json"},
            "yahoo": {"app_file": "credentials/yahoo_app.json", "token_file": "credentials/yahoo_token.json",
                      "redirect_uri": "https://localhost:8080"},
            "fantasynerds": {"api_key_file": "credentials/fantasynerds_key.txt"}}
    if os.path.exists(BOOTSTRAP_PATH):
        data = _deep_merge(data, _read_json(BOOTSTRAP_PATH))
    return data


def save_bootstrap(data):
    _write_json(BOOTSTRAP_PATH, data)


def load_default_config():
    """Charge et valide la configuration livrée, sans consulter la copie locale."""
    data = _read_json(DEFAULTS_PATH) if os.path.exists(DEFAULTS_PATH) else {}
    _validate_config_structure(data)
    validate_sources_config(data.get("sources", {}))
    for key in ("league", "settings", "sources", "grids", "aliases"):
        data.setdefault(key, {})
    return data


def load_config():
    """Configuration complète {league, settings, sources, grids, aliases} : copie locale de l'onglet
    config (config/config.json), sinon valeurs livrées (config/defaults.json)."""
    path = CONFIG_PATH if os.path.exists(CONFIG_PATH) else DEFAULTS_PATH
    data = _read_json(path) if os.path.exists(path) else {}
    _validate_config_structure(data)
    validate_sources_config(data.get("sources", {}))
    for key, empty in (("league", {}), ("settings", {}), ("sources", {}), ("grids", {}), ("aliases", {})):
        data.setdefault(key, empty)
    if path != DEFAULTS_PATH and os.path.exists(DEFAULTS_PATH):
        # copie locale créée avant l'ajout d'une source : la source (et ses nouveaux réglages) viennent
        # des valeurs livrées
        delivered_data = _read_json(DEFAULTS_PATH)
        _validate_config_structure(delivered_data)
        validate_sources_config(delivered_data.get("sources", {}))
        delivered = delivered_data.get("sources", {})
        for name, conf in delivered.items():
            local = data["sources"].setdefault(name, copy.deepcopy(conf))
            for key, value in conf.items():      # réglages ajoutés depuis (ex. players_url)
                local.setdefault(key, copy.deepcopy(value))
    return _validate_config_structure(data)


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
    return _effective_settings(load_config())


def enabled_sources(settings, stage=None):
    stage = stage or settings["active_stage"]
    return [name for name, flags in settings["sources"].items() if _stage_flags(flags).get(stage)]


def load_source_config(source_name):
    """Réglages propres à une source (URL / fichiers, colonnes...)."""
    sources = load_sources_config()
    if source_name not in sources:
        raise KeyError(f"Source '{source_name}' absente de la configuration (onglet settings, sections Sources)")
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
               "roster_source": "yahoo"},
    "games": {"per_slot": 82, "lineup": "daily"},
    "draft": {
        "type": "snake", "rounds": 12, "my_team": "", "order": [], "keepers": {}, "keeper_rounds": [], "picks_source": "sheet",
        "candidates": 40, "simulations": 200, "simulations_min": 10, "finalists": 5,
        "prune_z": 2.0, "time_budget": 8, "adp_noise": 0.15, "opponent_model": "need", "need_weight": 2.0, "need_candidates": 4,
    },
    "google_sheets": {
        "draft_spreadsheet_id": "", "config_tab": "settings",
        "reco_tab": "draft_reco", "projections_spreadsheet_id": "", "projections_tab": "bdd",
        "yahoo_tab": "yahoo", "detail_tab": "bdd_detail",
        "projections_start_col": "C", "poll_seconds": 10, "write_picks_to_sheet": True, "season_tab": "season",
        "alias_tab": "players",
    },
    # tirage au sort de l'ordre de draft (onglet board)
    "lottery": {"balls_range": "board!N8:O22", "managers_range": "board!B8:B22", "active_range": "board!L8:L22",
                "number_range": "board!AA9", "output_range": "lottery!A1"},
}

# Postes qui ne comptent pas dans le plafond de matchs (banc, blessés)
NON_STARTING_SLOTS = ("BN", "IL", "IL+")


def load_league():
    """Paramètres de la ligue complétés par les valeurs par défaut et le bootstrap (ID du classeur)."""
    return _effective_league(load_config())


def load_sources_config():
    sources = load_config()["sources"]
    return validate_sources_config(sources)


def load_aliases():
    """Alias de noms : {"nom vu dans une source": "nom canonique"} (onglet players)."""
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
