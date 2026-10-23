"""Import des projections DARKO (darko.app), modèle statistique mis à jour chaque jour.

Les données de la page Projections sont lues dans son JSON (https://www.darko.app/projections/__data.json,
format SvelteKit : chaque valeur est un index vers le tableau `data`). DARKO projette des stats pour
100 possessions, plus les minutes et le rythme de l'équipe : par match = pour 100 x rythme / 100 x minutes / 48.
Pas de matchs projetés : la source sert pour les minutes et les stats.
"""

import json
import logging

from scripts.season.schedule import TEAM_CODES
from scripts.sources.base import ProjectionSource

log = logging.getLogger(__name__)

PER_100 = {"pts": "x_pts_100", "ast": "x_ast_100", "stl": "x_stl_100", "blk": "x_blk_100",
           "tov": "x_tov_100", "fga": "x_fga_100", "fg3a": "x_fg3a_100", "fta": "x_fta_100"}
PCT = {"fgp": "x_fg_pct", "fg3p": "x_fg3_pct", "ftp": "x_ft_pct"}


class DarkoSource(ProjectionSource):
    name = "darko"
    label = "DARKO"
    per_game = True

    def page_requests(self):
        return [(self.url_template(), None)]

    def parse_page(self, text, _context):
        return parse_darko(json.loads(text))


def _hydrate(data):
    """Reconstruit l'objet racine d'un tableau SvelteKit (devalue) : dicts et listes d'index."""
    memo = {}

    def value(i):
        if i in memo:
            return memo[i]
        raw = data[i]
        if isinstance(raw, dict):
            out = {k: value(j) for k, j in raw.items()}
        elif isinstance(raw, list):
            out = [value(j) for j in raw]
        else:
            out = raw
        memo[i] = out
        return out

    return value(0)


def players_of(payload):
    for node in payload.get("nodes") or []:
        if node and node.get("type") == "data" and node.get("data"):
            root = _hydrate(node["data"])
            if isinstance(root, dict) and isinstance(root.get("players"), list):
                return root.get("players"), root.get("asOf")
    return [], None


def parse_darko(payload):
    players, as_of = players_of(payload)
    if as_of:
        log.info("[DARKO] Projections du %s : %d joueurs.", as_of, len(players))
    rows = []
    for p in players:
        minutes, pace = p.get("x_minutes"), p.get("x_pace")
        if not p.get("player_name") or not minutes or not pace or minutes < 1:
            continue
        factor = pace / 100 * minutes / 48
        stats = {cat: round(p[key] * factor, 3) for cat, key in PER_100.items() if p.get(key) is not None}
        if p.get("x_orb_100") is not None and p.get("x_drb_100") is not None:
            stats["reb"] = round((p["x_orb_100"] + p["x_drb_100"]) * factor, 3)
        stats.update({cat: p[key] for cat, key in PCT.items() if p.get(key) is not None})
        stats["mpg"] = round(minutes, 2)
        rows.append({
            "player": p["player_name"],
            "team": TEAM_CODES.get(p.get("team_name") or "", ""),
            "positions": [x for x in (p.get("position") or "").replace("-", ",").split(",") if x],
            "stats": stats,
        })
    return rows
