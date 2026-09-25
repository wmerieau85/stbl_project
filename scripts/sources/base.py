"""Logique commune aux imports de projections.

Chaque source ne définit que ce qui lui est propre : les URL à appeler et la façon
de lire une page (fonction `parse_page`). Le reste (standardisation des colonnes,
conversion des nombres, échelle des pourcentages, enregistrement) est partagé.
"""

import logging

from scripts.config import load_settings, load_source_mapping
from scripts.db import PERCENT_COLUMNS, STAT_COLUMNS, replace_projections
from scripts.http_client import HttpClient
from scripts.names import clean_display_name, name_key

log = logging.getLogger(__name__)

EMPTY_VALUES = {"", "—", "–", "-", "N/A", "NA", "nan"}
POSITION_ORDER = ["PG", "SG", "G", "SF", "PF", "F", "C"]

# Codes équipe harmonisés sur les abréviations officielles NBA
TEAM_CODES = {
    "GS": "GSW", "NO": "NOP", "NOR": "NOP", "NY": "NYK", "SA": "SAS",
    "UTH": "UTA", "PHO": "PHX", "WSH": "WAS", "BRK": "BKN", "CHO": "CHA",
}


def parse_number(value):
    """ "2,394" -> 2394.0 ; "48.7%" -> 48.7 ; "—" -> None."""
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("%", "").replace("\xa0", "")
    if text in EMPTY_VALUES:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def normalize_team(team):
    code = (team or "").strip().upper()
    return TEAM_CODES.get(code, code)


def sort_positions(positions):
    unique = {p.strip().upper() for p in positions if p and p.strip()}
    return ",".join(sorted(unique, key=lambda p: (POSITION_ORDER.index(p) if p in POSITION_ORDER else 99, p)))


def standardize_stats(raw_stats, columns_map, percent_scale):
    """Renomme les colonnes selon mappings.json et convertit les valeurs."""
    stats = {}
    for source_col, value in raw_stats.items():
        std_col = columns_map.get(source_col.strip().lower())
        if std_col not in STAT_COLUMNS:
            continue
        number = parse_number(value)
        if number is not None and std_col in PERCENT_COLUMNS and percent_scale != 1:
            number = round(number / percent_scale, 4)
        stats[std_col] = number
    return stats


class ProjectionSource:
    """Classe de base d'une source de projections."""

    name = ""  # clé dans config/mappings.json
    label = ""  # valeur enregistrée dans la colonne `source`

    def __init__(self, settings=None, http=None):
        self.settings = settings or load_settings()
        self.mapping = load_source_mapping(self.name)
        self.http = http or HttpClient.from_settings(self.settings)
        self.season = self.settings["active_season"]
        self.stage = self.settings["active_stage"]
        self.columns_map = {k.strip().lower(): v for k, v in self.mapping["columns"].items()}
        self.percent_scale = self.mapping.get("percent_scale", 1)

    # --- à définir par chaque source -------------------------------------
    def page_requests(self):
        """Liste de (url, contexte) à télécharger. Le contexte est passé à parse_page."""
        raise NotImplementedError

    def parse_page(self, html, context):
        """Retourne une liste de dicts {"player", "team", "positions": [...], "stats": {col: valeur brute}}."""
        raise NotImplementedError

    # --- logique commune ---------------------------------------------------
    def url_template(self):
        template = self.mapping.get("urls", {}).get(self.stage)
        if not template:
            raise ValueError(f"[{self.label}] Aucune URL pour l'étape '{self.stage}' dans mappings.json")
        return template

    def fetch(self):
        """Télécharge toutes les pages et fusionne les joueurs présents sur plusieurs pages."""
        merged = {}
        failures = 0
        requests = self.page_requests()
        for url, context in requests:
            log.info("[%s] Téléchargement %s", self.label, url)
            html = self.http.get_text(url)
            if html is None:
                failures += 1
                continue
            rows = self.parse_page(html, context)
            if not rows:
                log.error("[%s] Aucun joueur lu sur %s", self.label, url)
                failures += 1
                continue
            for row in rows:
                display = clean_display_name(row["player"])
                key = name_key(display)
                if not key:
                    continue
                team = normalize_team(row.get("team"))
                merge_key = (key, team)
                if merge_key in merged:
                    # Joueur éligible à plusieurs postes : on cumule les positions
                    merged[merge_key]["positions"].update(row.get("positions") or [])
                    continue
                merged[merge_key] = {
                    "player": display,
                    "player_key": key,
                    "team": team,
                    "positions": set(row.get("positions") or []),
                    **standardize_stats(row["stats"], self.columns_map, self.percent_scale),
                }
        if failures:
            # Import partiel = données incomplètes : on préfère ne rien remplacer
            log.error("[%s] %d/%d page(s) en échec.", self.label, failures, len(requests))
            return None
        for record in merged.values():
            record["positions"] = sort_positions(record["positions"])
            record.update(source=self.label, season=self.season, stage=self.stage)
        return list(merged.values())

    def run(self):
        records = self.fetch()
        if records is None:
            log.error("[%s] Import annulé. Les données existantes sont conservées.", self.label)
            return False
        count = replace_projections(records, self.label, self.season, self.stage)
        log.info("[%s] %d joueurs enregistrés (%s, %s).", self.label, count, self.season, self.stage)
        return True
