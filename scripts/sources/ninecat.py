"""Import 9cat.co.il (9 Fantasy) : projections « Expected » de la page Players, via l'API publique
api.fantasy.365scores.com (JSON, sans compte).

- étapes draft / ros : period=projected (moyennes par match + matchs projetés) ;
- étape sea : period=season (stats réelles par match), ignorée tant que la saison n'a pas commencé
  (sinon ce seraient les stats de la saison précédente).

Les stats sont identifiées par un numéro (Transco | <colonne>, ex. 2 = PTS, 3 = FGM, 4 = FGA...).
Équipe et postes viennent de la liste des joueurs (Sources | Players).
"""

import json
import logging

from scripts.sources.base import ProjectionSource

log = logging.getLogger(__name__)

POSITIONS = {18: "PG", 19: "SG", 20: "SF", 21: "PF", 22: "C", 6: "G", 7: "F"}


class NineCatSource(ProjectionSource):
    name = "ninecat"
    label = "9cat"
    per_game = True

    def page_requests(self):
        return [(self.url_template(), None)]

    def _players(self):
        url = self.source_config.get("players_url")
        text = self.http.get_text(url) if url else None
        if text is None:
            log.error("[9cat] Liste des joueurs indisponible (%s).", url)
            return None
        return json.loads(text)

    def documents(self):
        docs = super().documents()
        players = self._players()
        return [(label, content if players is not None else None, players) for label, content, _ in docs]

    def parse_page(self, text, players):
        return parse_ninecat(json.loads(text), players, self.stage)


def parse_ninecat(stats, players, stage="draft"):
    """stats : {id: {gp, stats: [{id, value}]}} ; players : {competition: {teams, players}}."""
    comp = next(iter(players.values()), {}) if players else {}
    teams = {str(k): t.get("abbr", "") for k, t in (comp.get("teams") or {}).items()}
    people = comp.get("players") or {}
    if isinstance(people, list):
        people = {str(p.get("id")): p for p in people}
    if stage not in ("draft", "ros") and not any((t or {}).get("gp") for t in (comp.get("teams") or {}).values()):
        log.warning("[9cat] Stats '%s' : la saison n'a pas commencé (stats de la saison précédente), ignorées.",
                    stage)
        return []
    rows = []
    for pid, entry in stats.items():
        person = people.get(str(pid))
        if not person or not entry.get("gp"):
            continue
        values = {str(s["id"]): s.get("value") for s in entry.get("stats") or []}
        values["gp"] = entry.get("gp")
        name = (person.get("metadata") or {}).get("englishName") or person.get("name") or ""
        rows.append({
            "player": name,
            "team": teams.get(str(person.get("teamId")), ""),
            "positions": [POSITIONS[p] for p in person.get("positions") or [] if p in POSITIONS],
            "stats": values,
        })
    return rows
