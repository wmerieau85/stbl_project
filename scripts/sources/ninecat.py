"""Import 9cat.co.il (9 Fantasy) : projections « Expected » de la page Players, via l'API publique
api.fantasy.365scores.com (JSON, sans compte).

- étapes draft / ros : period=projected (moyennes par match + matchs projetés) ;
- étape sea : period=season (stats réelles par match), ignorée tant que la saison n'a pas commencé
  (sinon ce seraient les stats de la saison précédente).

Les stats sont identifiées par un numéro (Transco | <colonne>, ex. 2 = PTS, 3 = FGM, 4 = FGA...).
Équipe et postes viennent de la liste des joueurs (Sources | Players).

Projections « modèles » (draft / ros) : 9cat donne à une cinquantaine de joueurs, surtout des recrues, des
valeurs par défaut (exactement 30 matchs et une poignée de MPG répétés : 22,6 / 26,1 / 21,7...). Une ligne
à TEMPLATE_GP matchs dont le MPG est partagé par au moins TEMPLATE_MIN_GROUP joueurs est écartée : le
joueur garde ses autres sources (sans aucune, il sort des projections ros).
"""

import json
import logging
from collections import Counter

from scripts.sources.base import ProjectionSource

log = logging.getLogger(__name__)

POSITIONS = {18: "PG", 19: "SG", 20: "SF", 21: "PF", 22: "C", 6: "G", 7: "F"}
TEMPLATE_GP = 30
TEMPLATE_MIN_GROUP = 3


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

    def fetch(self):
        records = super().fetch()
        if records is None or self.stage not in ("draft", "ros"):
            return records
        kept, dropped = drop_templates(records)
        if dropped:
            log.info("[9cat] %d projection(s) « modèle » écartée(s) (%d matchs, MPG répété) : %s%s", len(dropped),
                     TEMPLATE_GP, ", ".join(dropped[:15]), " ..." if len(dropped) > 15 else "")
        return kept


def _mpg(record):
    if record.get("mpg"):
        return round(record["mpg"], 1)
    if record.get("min") and record.get("gp"):
        return round(record["min"] / record["gp"], 1)
    return None


def drop_templates(records):
    """(lignes gardées, noms écartés) : TEMPLATE_GP matchs et MPG partagé par TEMPLATE_MIN_GROUP joueurs ou plus."""
    groups = Counter(_mpg(r) for r in records if r.get("gp") == TEMPLATE_GP)
    template = {m for m, n in groups.items() if m is not None and n >= TEMPLATE_MIN_GROUP}
    kept, dropped = [], []
    for r in records:
        if r.get("gp") == TEMPLATE_GP and _mpg(r) in template:
            dropped.append(r["player"])
        else:
            kept.append(r)
    return kept, dropped


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
