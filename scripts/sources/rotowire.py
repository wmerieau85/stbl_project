"""Import des projections RotoWire, publiées par l'API publique de Sleeper (JSON, sans compte).

https://api.sleeper.app/projections/nba/{year}?season_type=regular : une ligne par joueur
(`company` = rotowire), moyennes par match mises à jour chaque jour, tirs réussis / tentés compris.
Pas de matchs projetés (`gp` vaut toujours 1) ni de minutes : la source ne sert que pour les stats.
"""

import json
import logging

from scripts.sources.base import ProjectionSource

log = logging.getLogger(__name__)


class RotoWireSource(ProjectionSource):
    name = "rotowire"
    label = "RotoWire"
    per_game = True

    def page_requests(self):
        year = self.season.split("-")[0]           # saison 2026-27 = 2026 chez Sleeper
        return [(self.url_template().format(year=year), None)]

    def parse_page(self, text, _context):
        return parse_rotowire(json.loads(text))


def parse_rotowire(data):
    rows = []
    for entry in data or []:
        stats = dict(entry.get("stats") or {})
        person = entry.get("player") or {}
        name = " ".join(p for p in (person.get("first_name"), person.get("last_name")) if p)
        if not name or stats.get("pts") is None:
            continue
        if entry.get("company") and entry["company"] != "rotowire":
            continue
        stats.pop("gp", None)        # 1 = moyenne par match, pas un nombre de matchs
        stats.pop("min", None)       # renseigné pour une poignée de joueurs seulement
        rows.append({
            "player": name,
            "team": entry.get("team") or person.get("team") or "",
            "positions": person.get("fantasy_positions") or ([person["position"]] if person.get("position") else []),
            "stats": stats,
        })
    return rows
