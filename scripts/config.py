"""Chemins du projet et chargement de la configuration (source unique)."""

import copy
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
DB_PATH = os.path.join(BASE_DIR, "database.sqlite")
SETTINGS_PATH = os.path.join(CONFIG_DIR, "settings.json")
MAPPINGS_PATH = os.path.join(CONFIG_DIR, "mappings.json")
ALIASES_PATH = os.path.join(CONFIG_DIR, "player_aliases.json")
WEIGHTS_DIR = os.path.join(CONFIG_DIR, "weights")
EXPORTS_DIR = os.path.join(BASE_DIR, "exports")

# Valeurs par défaut : complétées/écrasées par config/settings.json s'il existe.
DEFAULT_SETTINGS = {
    "active_season": "2026-27",
    "active_stage": "draft",  # "draft" ou "ros"
    "sources": {"cbs": True, "fantasypros": True, "fanscout": True, "draftkick": True, "lineupexperts": True},
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


def load_source_mapping(source_name):
    """Retourne le bloc de config/mappings.json propre à une source."""
    mappings = _read_json(MAPPINGS_PATH)
    if source_name not in mappings:
        raise KeyError(f"Source '{source_name}' absente de config/mappings.json")
    return mappings[source_name]


def load_all_mappings():
    return _read_json(MAPPINGS_PATH)


def load_aliases():
    """Alias de noms : {"nom vu dans une source": "nom canonique"}."""
    if os.path.exists(ALIASES_PATH):
        return _read_json(ALIASES_PATH)
    return {}
