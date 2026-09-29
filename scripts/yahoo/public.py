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


# --- saison : équipes, effectifs, classement ---------------------------------------------

_TEAM_LINK = re.compile(r"/nba/(\d+)/(\d+)/?$")
SLOTS = {"PG", "SG", "G", "SF", "PF", "F", "C", "UTIL", "BN", "IL", "IL+"}


def _num(text):
    text = (text or "").strip().replace(",", "").replace("*", "")
    if text in ("", "-", "–", "—"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def teams(league_id, timeout=30, session=None):
    """[{team_id, name}] d'après les liens de la page de classement."""
    soup = BeautifulSoup(fetch("standings?opt_out=1", league_id, timeout, session), "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        m = _TEAM_LINK.search(a["href"].split("?")[0])
        if m and m.group(1) == str(league_id) and m.group(2) not in seen:
            name = a.get_text(" ", strip=True)
            if name:
                seen.add(m.group(2))
                out.append({"team_id": int(m.group(2)), "name": name})
    return out


def roster(league_id, team_id, timeout=30, session=None):
    """Effectif d'une équipe et compteur de matchs par poste.

    Retourne {"players": [{player, player_id, nba_team, positions, slot, status}],
              "games": {"G": {"played", "remaining", "max"}, ...}}
    """
    soup = BeautifulSoup(fetch(str(team_id), league_id, timeout, session), "html.parser")
    players = []
    for row in soup.select("table tbody tr"):
        link = row.select_one("a.name") or row.select_one("div.ysf-player-name a")
        if link is None:
            continue
        name = link.get("title") or link.get_text(" ", strip=True)   # le texte est abrégé (« N. Alexander-Walker »)
        pid = link.get("data-ys-playerid", "")
        info = row.select_one("span.Fz-xxs")
        nba_team, positions = "", ""
        if info and " - " in info.get_text():
            nba_team, positions = [x.strip() for x in info.get_text(" ", strip=True).split(" - ", 1)]
        slot = ""
        first = row.find("td")
        first_text = first.get_text(" ", strip=True).upper() if first else ""
        if first_text in SLOTS:
            slot = first_text
        elif "bench" in (row.get("class") or []):
            slot = "BN"
        status_el = row.select_one("span.F-injury") or row.select_one("abbr.F-injury")
        status = status_el.get_text(" ", strip=True) if status_el else ""
        players.append({"player": name, "player_id": pid, "nba_team": nba_team.upper(),
                        "positions": positions, "slot": slot, "status": status})
    games = {}
    for table in soup.find_all("table"):
        heads = [th.get_text(" ", strip=True).lower() for th in table.find_all("th")]
        if heads[:3] == ["pos", "played", "remaining"]:
            for tr in table.select("tbody tr"):
                cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                if len(cells) >= 5:
                    games[cells[0].upper()] = {"played": _num(cells[1]) or 0.0, "remaining": _num(cells[2]),
                                               "max": _num(cells[4])}
    return {"players": players, "games": games}


STANDINGS_STATS = {"fg%": "fgp", "ft%": "ftp", "3ptm": "fg3m", "pts": "pts", "reb": "reb", "ast": "ast",
                   "st": "stl", "blk": "blk", "to": "tov", "gp": "gp", "total": "total"}


def standings(league_id, timeout=30, session=None):
    """{nom d'équipe: {"points": {cat: pts, "total": x}, "stats": {cat: valeur, "gp": n}, "rank": r}}.

    Page classement : 1er tableau = points roto par catégorie, 2e = stats (avec GP).
    Valeurs None tant que la saison n'a pas commencé.
    """
    soup = BeautifulSoup(fetch("standings?opt_out=1", league_id, timeout, session), "html.parser")
    out = {}
    tables = [t for t in soup.find_all("table") if t.select("tbody tr a")]
    for k, table in enumerate(tables[:2]):
        header_rows = table.find("thead").find_all("tr") if table.find("thead") else []
        heads = [th.get_text(" ", strip=True).lower() for th in (header_rows[-1].find_all("th") if header_rows else [])]
        kind = "points" if k == 0 else "stats"
        for tr in table.select("tbody tr"):
            cells = tr.find_all("td")
            link = tr.find("a")
            if not link or len(cells) != len(heads):
                continue
            team = link.get_text(" ", strip=True)
            entry = out.setdefault(team, {"points": {}, "stats": {}, "rank": None})
            for head, cell in zip(heads, cells):
                key = STANDINGS_STATS.get(head)
                if head == "rank" and kind == "points":
                    entry["rank"] = _num(cell.get_text())
                elif key:
                    entry[kind][key] = _num(cell.get_text())
    return out
