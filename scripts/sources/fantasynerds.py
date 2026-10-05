"""Import Fantasy Nerds (API payante, clé personnelle) : projections de draft NBA.

La clé n'est jamais dans le dépôt : variable d'environnement FANTASYNERDS_API_KEY, sinon fichier
indiqué dans config/bootstrap.json (fantasynerds.api_key_file, par défaut
credentials/fantasynerds_key.txt, une seule ligne). La clé TEST renvoie des données d'exemple
(saison 2021) : pratique pour vérifier le branchement, pas pour la draft.

Totaux sur la saison ; FG% et FT% sans les tentatives (estimées ensuite par la pondération).
"""

import json
import logging
import os

from scripts.config import BASE_DIR, load_bootstrap
from scripts.http_client import safe_url
from scripts.sources.base import ProjectionSource

log = logging.getLogger(__name__)


def api_key():
    key = os.environ.get("FANTASYNERDS_API_KEY", "").strip()
    if key:
        return key
    path = (load_bootstrap().get("fantasynerds") or {}).get("api_key_file") or "credentials/fantasynerds_key.txt"
    path = path if os.path.isabs(path) else os.path.join(BASE_DIR, path)
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:
            return f.read().strip()
    return ""


class FantasyNerdsSource(ProjectionSource):
    name = "fantasynerds"
    label = "FantasyNerds"

    def page_requests(self):
        key = api_key()
        if not key:
            log.error("[FantasyNerds] Clé API absente : variable FANTASYNERDS_API_KEY ou fichier "
                      "credentials/fantasynerds_key.txt (voir config/bootstrap.json).")
            return []
        return [(self.url_template().format(apikey=key), None)]

    def documents(self):
        requests = self.page_requests()
        docs = []
        for url, context in requests:
            log.info("[FantasyNerds] Téléchargement %s", safe_url(url))
            docs.append(("API Fantasy Nerds", self.http.get_text(url), context))
        return docs

    def parse_page(self, text, _context):
        data = json.loads(text)
        if isinstance(data, dict) and data.get("error"):
            log.error("[FantasyNerds] %s", data["error"])
            return []
        start = int(self.season.split("-")[0])
        season = data.get("season")
        if season and int(season) not in (start, start + 1):
            log.error("[FantasyNerds] Projections de la saison %s, pas %s (clé TEST ou projections pas encore "
                      "publiées) : ignorées.", season, self.season)
            return []
        return parse_fantasynerds(data)


def parse_fantasynerds(data):
    rows = []
    for p in data.get("projections") or []:
        stats = {k: v for k, v in p.items() if k not in ("playerId", "name", "position", "team")}
        rows.append({"player": p.get("name", ""), "team": p.get("team", ""),
                     "positions": [x for x in str(p.get("position", "")).replace("/", ",").split(",") if x],
                     "stats": stats})
    return rows
