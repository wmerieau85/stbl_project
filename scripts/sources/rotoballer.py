"""Import des projections de classement RotoBaller (moyennes par match, ligue 9-cat)."""

import json

from scripts.sources.base import ProjectionSource


class RotoBallerSource(ProjectionSource):
    name = "rotoballer"
    label = "RotoBaller"
    per_game = True

    def page_requests(self):
        return [(self.url_template(), None)]

    def parse_page(self, text, _context):
        data = json.loads(text)
        if data.get("format") != "9cat":
            raise ValueError(f"[RotoBaller] Format de projections inattendu : {data.get('format')!r}.")
        return parse_rotoballer(data)


def parse_rotoballer(data):
    rows = []
    for player in data.get("data") or []:
        projection = player.get("proj") or {}
        if not player.get("name") or not projection:
            continue
        rows.append({
            "player": player["name"],
            "team": player.get("team", ""),
            "positions": [player["position"]] if player.get("position") else [],
            "stats": projection,
        })
    return rows
