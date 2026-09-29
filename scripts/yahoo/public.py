"""Lecture des pages publiques de la ligue Yahoo (sans API ni connexion).

Solution de secours tant que l'accès à l'API Fantasy n'est pas validé par Yahoo : la ligue
étant publique, la page des résultats de draft est lisible sans être connecté.

    https://basketball.fantasysports.yahoo.com/nba/<ID>/draftresults
        un tableau par tour : n° du choix, joueur (vide tant que le choix n'est pas fait), équipe
"""

import logging
import re

import requests
from bs4 import BeautifulSoup

from scripts.names import clean_display_name

log = logging.getLogger(__name__)

BASE = "https://basketball.fantasysports.yahoo.com/nba"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/128.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.8",
}
_ROUND = re.compile(r"Round\s+(\d+)", re.I)
# "Nikola Jokic DEN - C" / "Nikola Jokic (DEN - C)" -> "Nikola Jokic"
_TEAM_POS = re.compile(r"\s+[A-Z]{2,4}\s+-\s+[A-Z,/]+$")


class PublicPageError(RuntimeError):
    pass


def fetch(path, league_id, timeout=30, session=None):
    url = f"{BASE}/{league_id}/{path}"
    resp = (session or requests).get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    if "login.yahoo.com" in resp.url:
        raise PublicPageError(f"{url} demande une connexion : la ligue n'est pas (ou plus) publique.")
    if resp.status_code != 200:
        raise PublicPageError(f"{url} : code HTTP {resp.status_code}")
    if "There was a problem" in resp.text[:5000]:
        raise PublicPageError(f"{url} : Yahoo signale un problème (ID de ligue incorrect ?)")
    return resp.text


def _player_name(cell):
    link = cell.find("a")
    text = link.get_text(" ", strip=True) if link else cell.get_text(" ", strip=True)
    text = text.replace("\xa0", " ").strip()
    if not text:
        return "", ""
    text = _TEAM_POS.sub("", clean_display_name(text)).strip()
    href = link.get("href", "") if link else ""
    m = re.search(r"/players/(\d+)", href)
    return text, (m.group(1) if m else "")


def parse_draft_results(html):
    """[{pick, round, pick_in_round, team, player, player_id}] - un élément par choix, fait ou non."""
    soup = BeautifulSoup(html, "html.parser")
    picks = []
    for table in soup.find_all("table"):
        head = table.find("th")
        m = _ROUND.search(head.get_text(" ", strip=True)) if head else None
        if not m:
            continue
        rnd = int(m.group(1))
        for row in table.select("tbody tr"):
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            num = re.sub(r"\D", "", cells[0].get_text())
            if not num:
                continue
            team = cells[-1].get("title") or cells[-1].get_text(" ", strip=True)
            player, player_id = _player_name(cells[1])
            picks.append({"round": rnd, "pick_in_round": int(num), "team": team.strip(),
                          "player": player, "player_id": player_id})
    picks.sort(key=lambda p: (p["round"], p["pick_in_round"]))
    for i, p in enumerate(picks, 1):
        p["pick"] = i
    return picks


def draft_results(league_id, timeout=30, session=None):
    picks = parse_draft_results(fetch("draftresults", league_id, timeout, session))
    if not picks:
        raise PublicPageError("Aucun tableau de draft trouvé sur la page publique (format Yahoo modifié ?)")
    return picks


def team_names(league_id, timeout=30, session=None):
    """Noms des équipes (page Managers). Les managers sont masqués sur les pages publiques."""
    soup = BeautifulSoup(fetch("teams", league_id, timeout, session), "html.parser")
    names = []
    for row in soup.select("table tbody tr"):
        cell = row.find("td")
        if cell:
            names.append(cell.get_text(" ", strip=True))
    return names
