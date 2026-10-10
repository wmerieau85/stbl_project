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


def _season_year(season):
    if season is None:
        return None
    match = re.fullmatch(r"(\d{4})(?:-\d{2})?", str(season))
    if not match:
        raise ValueError(f"Saison Yahoo invalide : {season!r}")
    return match.group(1)


def fetch(path, league_id, timeout=30, session=None, season=None):
    season_year = _season_year(season)
    url = f"https://basketball.fantasysports.yahoo.com/{season_year}/nba/{league_id}/{path}" \
        if season_year else f"{BASE}/{league_id}/{path}"
    return _fetch_url(url, timeout, session)


def _fetch_url(url, timeout=30, session=None):
    resp = (session or requests).get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    if "login.yahoo.com" in resp.url:
        raise PublicPageError(f"{url} demande une connexion : la ligue n'est pas (ou plus) publique.")
    if resp.status_code != 200:
        raise PublicPageError(f"{url} : code HTTP {resp.status_code}")
    if "There was a problem" in resp.text[:5000]:
        raise PublicPageError(f"{url} : Yahoo signale un problème (ID de ligue incorrect ?)")
    return resp.text


_TEAM_LOG_STATS = {
    "gp*": "gp", "fgm/a*": "fgm_a", "fg%": "fgp", "ftm/a*": "ftm_a",
    "ft%": "ftp", "3ptm": "fg3m", "pts": "pts", "reb": "reb", "ast": "ast",
    "st": "stl", "blk": "blk", "to": "tov",
}


def parse_team_log(html):
    """[{player, player_id, gp, fgm, fga, fgp, ftm, fta, ftp, fg3m, pts, reb, ast, stl, blk, tov}]."""
    soup = BeautifulSoup(html, "html.parser")
    table = None
    indices = {}
    for candidate in soup.find_all("table"):
        header_rows = candidate.select("thead tr")
        for header_row in reversed(header_rows):
            cells = header_row.find_all(["th", "td"], recursive=False)
            labels = [cell.get_text(" ", strip=True).lower() for cell in cells]
            found = {key: labels.index(label) for label, key in _TEAM_LOG_STATS.items() if label in labels}
            if "name" in labels and "gp" in found and "fgm_a" in found and "ftm_a" in found:
                table, indices = candidate, found
                indices["name"] = labels.index("name")
                break
        if table is not None:
            break
    if table is None:
        return []

    players = []
    for row in table.select("tbody tr"):
        cells = row.find_all(["td", "th"], recursive=False)
        name_index = indices["name"]
        if len(cells) <= name_index:
            continue
        link = row.select_one('a[href*="/players/"]')
        if link is None:
            continue
        name_cell = cells[name_index]
        name = link.get("title") or link.get_text(" ", strip=True) or name_cell.get_text(" ", strip=True)
        if not name:
            continue
        href = link.get("href", "")
        player_id_match = re.search(r"/players/(\d+)", href)
        row_data = {
            "player": name,
            "player_id": player_id_match.group(1) if player_id_match else "",
        }
        for key, field in (("gp", "gp"), ("fgp", "fgp"), ("ftp", "ftp"), ("fg3m", "fg3m"),
                           ("pts", "pts"), ("reb", "reb"), ("ast", "ast"), ("stl", "stl"),
                           ("blk", "blk"), ("tov", "tov")):
            index = indices.get(key)
            row_data[field] = _num(cells[index].get_text(" ", strip=True)) if index is not None and index < len(cells) else None
        for source, made_key, attempt_key in (("fgm_a", "fgm", "fga"), ("ftm_a", "ftm", "fta")):
            index = indices[source]
            ratio = cells[index].get_text(" ", strip=True) if index < len(cells) else ""
            match = re.fullmatch(r"\s*([\d,]+)\s*/\s*([\d,]+)\s*", ratio)
            row_data[made_key] = _num(match.group(1)) if match else None
            row_data[attempt_key] = _num(match.group(2)) if match else None
        players.append(row_data)
    return players


def team_log(league_id, team_id, season, timeout=30, session=None):
    """Statistiques cumulées des joueurs ayant rapporté des points à une équipe."""
    season_year = _season_year(season)
    if not season_year:
        raise ValueError("Une saison est obligatoire pour lire un Team Log Yahoo.")
    url = f"https://basketball.fantasysports.yahoo.com/{season_year}/nba/{league_id}/{team_id}/teamlog"
    players = parse_team_log(_fetch_url(url, timeout, session))
    if not players:
        raise PublicPageError(
            f"{url} : aucun joueur dans le Team Log (Yahoo ne le publie qu'après la fin de la première semaine)."
        )
    return players


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


def teams(league_id, timeout=30, session=None, season=None):
    """[{team_id, name}] d'après les liens de la page de classement."""
    soup = BeautifulSoup(fetch("standings?opt_out=1", league_id, timeout, session, season), "html.parser")
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


def standings(league_id, timeout=30, session=None, season=None):
    """{nom d'équipe: {"points": {cat: pts, "total": x}, "stats": {cat: valeur, "gp": n}, "rank": r}}.

    Page classement : 1er tableau = points roto par catégorie, 2e = stats (avec GP).
    Valeurs None tant que la saison n'a pas commencé.
    """
    soup = BeautifulSoup(fetch("standings?opt_out=1", league_id, timeout, session, season), "html.parser")
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
