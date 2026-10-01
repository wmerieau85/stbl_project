"""Logique commune aux imports de projections.

Chaque source ne définit que ce qui lui est propre : où trouver ses documents
(pages web ou fichiers, méthode `documents`) et comment les lire (`parse_page`).
Le reste (standardisation des colonnes, conversion des nombres, échelle des
pourcentages, stats dérivées, enregistrement) est partagé.
"""

import logging

from scripts.config import load_settings, load_source_config
from scripts.db import NUMERIC_COLUMNS, PERCENT_COLUMNS, replace_projections
from scripts.http_client import HttpClient
from scripts.names import clean_display_name, name_key

log = logging.getLogger(__name__)

EMPTY_VALUES = {"", "—", "–", "-", "N/A", "NA", "nan"}
POSITION_ORDER = ["PG", "SG", "G", "SF", "PF", "F", "C"]

# Codes équipe harmonisés sur les abréviations officielles NBA
TEAM_CODES = {
    "GS": "GSW", "NO": "NOP", "NOR": "NOP", "NY": "NYK", "SA": "SAS",
    "UTH": "UTA", "UTAH": "UTA", "PHO": "PHX", "WSH": "WAS", "BRK": "BKN", "CHO": "CHA",
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


# Stats cumulables : multipliées par gp quand une source fournit des moyennes par match
COUNTING_COLUMNS = {
    "pts", "reb", "ast", "stl", "blk", "tov", "fgm", "fga", "ftm", "fta", "fg3m", "fg3a", "fpts",
}


def standardize_stats(raw_stats, columns_map, percent_scale):
    """Renomme les colonnes selon la configuration (Transco) et convertit les valeurs."""
    stats = {}
    for source_col, value in raw_stats.items():
        std_col = columns_map.get(source_col.strip().lower())
        if std_col not in NUMERIC_COLUMNS:
            continue
        number = parse_number(value)
        if number is not None and std_col in PERCENT_COLUMNS:
            number = round(number / percent_scale, 4)
        elif number is not None:
            number = round(number, 2)
        stats[std_col] = number
    return stats


def _ratio(numerator, denominator, digits=4):
    if numerator is None or not denominator:
        return None
    return round(numerator / denominator, digits)


def complete_stats(stats, per_game=False):
    """Harmonise les stats : totaux sur la saison et colonnes dérivables calculées.

    - per_game=True : les stats cumulables sont multipliées par gp, et `min` (par match)
      devient `mpg` ;
    - mpg = min / gp ; fgm = fgp x fga ; ftm = ftp x fta ; fg3p = fg3m / fg3a ;
      et inversement fgp = fgm / fga, etc. quand seul le pourcentage manque.
    """
    gp = stats.get("gp")
    if per_game:
        if stats.get("mpg") is None and stats.get("min") is not None:
            stats["mpg"] = stats.pop("min")
        for col in COUNTING_COLUMNS:
            if stats.get(col) is not None and gp is not None:
                stats[col] = round(stats[col] * gp, 2)

    if stats.get("min") is None and stats.get("mpg") is not None and gp is not None:
        stats["min"] = round(stats["mpg"] * gp, 1)
    if stats.get("mpg") is None:
        stats["mpg"] = _ratio(stats.get("min"), gp, 2)

    for made, attempts, pct in (("fgm", "fga", "fgp"), ("ftm", "fta", "ftp"), ("fg3m", "fg3a", "fg3p")):
        if stats.get(attempts) is None and stats.get(made) is not None and stats.get(pct):
            stats[attempts] = round(stats[made] / stats[pct], 2)
        if stats.get(made) is None and stats.get(pct) is not None and stats.get(attempts) is not None:
            stats[made] = round(stats[pct] * stats[attempts], 2)
        if stats.get(pct) is None:
            stats[pct] = _ratio(stats.get(made), stats.get(attempts))
    return stats


class ProjectionSource:
    """Classe de base d'une source de projections."""

    name = ""  # nom interne de la source (configuration des sources)
    label = ""  # valeur enregistrée dans la colonne `source`

    def __init__(self, settings=None, http=None):
        self.settings = settings or load_settings()
        self.source_config = load_source_config(self.name)
        self.http = http or HttpClient.from_settings(self.settings)
        self.season = self.settings["active_season"]
        self.stage = self.settings["active_stage"]
        self.columns_map = {k.strip().lower(): v for k, v in self.source_config["columns"].items()}
        self.percent_scale = self.source_config.get("percent_scale", 1)

    per_game = False  # True si la source fournit des moyennes par match

    # --- à définir par chaque source -------------------------------------
    def page_requests(self):
        """Sources web : liste de (url, contexte) à télécharger. Le contexte est passé à parse_page."""
        raise NotImplementedError

    def documents(self):
        """Liste de (libellé, contenu ou None si échec, contexte).

        Par défaut : téléchargement des pages web de `page_requests`. Les sources
        fichier (CSV) redéfinissent cette méthode.
        """
        docs = []
        for url, context in self.page_requests():
            log.info("[%s] Téléchargement %s", self.label, url)
            docs.append((url, self.http.get_text(url), context))
        return docs

    def parse_page(self, html, context):
        """Retourne une liste de dicts {"player", "team", "positions": [...], "stats": {col: valeur brute}}."""
        raise NotImplementedError

    # --- logique commune ---------------------------------------------------
    def url_template(self):
        template = self.source_config.get("urls", {}).get(self.stage)
        if not template:
            raise ValueError(f"[{self.label}] Aucune URL pour l'étape '{self.stage}' (onglet config, Sources | Draft / RoS)")
        return template

    def fetch(self):
        """Lit tous les documents et fusionne les joueurs présents dans plusieurs d'entre eux."""
        merged = {}
        failures = 0
        docs = self.documents()
        if not docs:
            return None
        for doc_label, content, context in docs:
            if content is None:
                failures += 1
                continue
            rows = self.parse_page(content, context)
            if not rows:
                log.error("[%s] Aucun joueur lu dans %s", self.label, doc_label)
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
                    **complete_stats(
                        standardize_stats(row["stats"], self.columns_map, self.percent_scale),
                        per_game=self.per_game,
                    ),
                }
        if failures:
            # Import partiel = données incomplètes : on préfère ne rien remplacer
            log.error("[%s] %d/%d document(s) en échec.", self.label, failures, len(docs))
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
