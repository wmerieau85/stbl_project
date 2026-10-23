"""Import des projections Net Prophet (netprophet.pro), gratuites et sans compte.

- page Rankings : moyennes par match (PTS, REB, AST, STL, BLK, 3PM, TO, FG%, FT%) de 300 joueurs ;
- page Games played (Sources | Players) : matchs projetés sur la saison complète (moyenne) et matchs joués
  la saison précédente.

Les matchs projetés portent sur toute la saison : ils sont ramenés aux matchs restants avec le calendrier
NBA (part des matchs de l'équipe qui restent à jouer). Les joueurs sans match la saison précédente
(rookies, absents toute la saison) reçoivent une valeur par défaut chez Net Prophet : leurs matchs ne
sont pas repris (les autres sources les donnent). Pas de minutes (elles ne sont que sur les fiches joueur).
"""

import logging
from datetime import date

from bs4 import BeautifulSoup

from scripts.names import name_key
from scripts.sources.base import ProjectionSource, normalize_team, parse_number

log = logging.getLogger(__name__)


class NetProphetSource(ProjectionSource):
    name = "netprophet"
    label = "Net Prophet"
    per_game = True

    def documents(self):
        url = self.url_template()
        games_url = self.source_config.get("players_url")
        log.info("[%s] Téléchargement %s", self.label, url)
        text = self.http.get_text(url)
        games = self.http.get_text(games_url) if games_url else None
        if games_url and games is None:
            log.warning("[%s] Matchs projetés indisponibles (%s) : stats seules.", self.label, games_url)
        return [(url, text, (games, remaining_share(self.season)))]

    def parse_page(self, text, context):
        games_html, share = context
        return parse_netprophet(text, games_html, share)


def remaining_share(season, today=None):
    """{équipe: part des matchs de la saison qui restent à jouer} ; None si le calendrier est indisponible."""
    try:
        from scripts.season.schedule import games_by_team, load_games
        games = load_games(season)
    except Exception as exc:     # pas de calendrier : pas de matchs projetés (les stats restent utilisables)
        log.warning("[Net Prophet] Calendrier NBA indisponible (%s) : matchs projetés ignorés.", exc)
        return None
    total = games_by_team(games, "0000-00-00")
    left = games_by_team(games, today or date.today())
    return {team: left.get(team, 0) / n for team, n in total.items() if n}


def _table(html):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        return [], []
    header = [th.get_text(" ", strip=True) for th in table.select("thead th")]
    rows = [[td.get_text(" ", strip=True) for td in tr.find_all("td")] for tr in table.select("tbody tr")]
    return header, [r for r in rows if len(r) == len(header)]


def _games(html):
    """{(clé nom, équipe): (matchs projetés, matchs la saison précédente)}."""
    header, rows = _table(html or "")
    find = lambda start: next((i for i, h in enumerate(header) if h.lower().startswith(start)), None)
    i_name, i_team, i_proj = find("player"), find("team"), find("projected gp")
    i_prev = next((i for i, h in enumerate(header) if h.lower().startswith("gp ")), None)
    if None in (i_name, i_team, i_proj):
        return {}
    out = {}
    for r in rows:
        prev = parse_number(r[i_prev]) if i_prev is not None else None
        out[(name_key(r[i_name]), normalize_team(r[i_team]))] = (parse_number(r[i_proj]), prev)
    return out


def parse_netprophet(html, games_html=None, share=None):
    header, rows = _table(html)
    cols = {h.lower(): i for i, h in enumerate(header)}
    if "player" not in cols or "pts" not in cols:
        return []
    games = _games(games_html) if games_html else {}
    if games_html and not games:
        log.warning("[Net Prophet] Tableau des matchs projetés illisible : stats seules.")
    out = []
    for r in rows:
        team = normalize_team(r[cols["team"]]) if "team" in cols else ""
        stats = {h: r[i] for h, i in cols.items() if h not in ("#", "player", "team", "pos")}
        projected, previous = games.get((name_key(r[cols["player"]]), team), (None, None))
        if projected and previous and share is not None and team in share:
            stats["gp"] = round(projected * share[team], 1)
        out.append({
            "player": r[cols["player"]],
            "team": team,
            "positions": [p for p in r[cols["pos"]].split("/") if p] if "pos" in cols else [],
            "stats": stats,
        })
    return out
