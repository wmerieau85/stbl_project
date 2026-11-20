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


class TeamLogNotPublished(PublicPageError):
    """Team Log vide : Yahoo ne le publie qu'après la fin de la première semaine de jeu."""


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
        raise TeamLogNotPublished(
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
        # « CHA - G,F » ; le statut de blessure (« Q ») a la même classe Fz-xxs et vient avant
        info = next((s for s in row.select("span.Fz-xxs") if " - " in s.get_text()), None)
        nba_team, positions = "", ""
        if info is not None:
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


# --- Transactions (add / drop / échanges) --------------------------------------------------------

TX_PAGE = 25            # transactions par page Yahoo (?count=0, 25, 50...)
TX_MAX_PAGES = 60
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                       "nov", "dec"), 1)}


def _tx_date(text, season):
    """« Nov 20, 10:13 am » -> ('2026-11-20', '10:13') ; année déduite de la saison (juil.-déc. = 1re année)."""
    m = re.match(r"\s*([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),\s*(\d{1,2}:\d{2}\s*[ap]m)?", text or "")
    if not m or m.group(1).lower() not in _MONTHS:
        return "", (text or "").strip()
    month, day = _MONTHS[m.group(1).lower()], int(m.group(2))
    first = int(_season_year(season)) if season else None
    year = (first if month >= 7 else first + 1) if first else None
    time = (m.group(3) or "").replace(" ", "")
    if time:
        hh, rest = time.split(":")
        hour = int(hh) % 12 + (12 if rest.endswith("pm") else 0)
        time = f"{hour:02d}:{rest[:2]}"
    return (f"{year:04d}-{month:02d}-{day:02d}" if year else f"{month:02d}-{day:02d}"), time


def _tx_player(block):
    link = block.select_one('a[href*="/nba/players/"][target="sports"]') or block.select_one('a[href*="/players/"]')
    if link is None:
        return None
    pid = re.search(r"/players/(\d+)", link.get("href", ""))
    pos = block.select_one(".F-position")
    nba_team, positions = "", ""
    if pos is not None and " - " in pos.get_text():
        nba_team, positions = (x.strip() for x in pos.get_text().split(" - ", 1))
    return {"player": clean_display_name(link.get_text(" ", strip=True)), "player_id": pid.group(1) if pid else "",
            "nba_team": nba_team, "positions": positions}


def _tx_team(cell):
    link = cell.select_one('a[href*="/nba/"]') if cell is not None else None
    stamp = cell.select_one(".F-timestamp") if cell is not None else None
    team_id = re.search(r"/nba/\d+/(\d+)", link.get("href", "")) if link is not None else None
    return (team_id.group(1) if team_id else "", link.get_text(" ", strip=True) if link is not None else "",
            stamp.get_text(" ", strip=True) if stamp is not None else "")


def parse_transactions(html, season=None):
    """Page Transactions -> ([transaction], page suivante ?). Une ligne par joueur :
    {date, time, type (add / drop / trade), team_id, team, player, player_id, nba_team, positions, detail,
    other_team_id, other_team, transaction}. Échange : un bloc de deux lignes (icône F-trade, rowspan=2),
    chacune « joueurs - Traded to - équipe qui les reçoit » ; other_team = équipe d'origine. Les lignes
    « Trading List » (joueurs proposés à l'échange) sont ignorées."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.Tst-transaction-table")
    nav = soup.select_one(".pagingnavlist")
    has_next = nav is not None and any("next" in a.get_text().lower() for a in nav.select("a"))
    if table is None:
        return [], False
    rows = table.find_all("tr")
    out, i, seq = [], 0, 0
    while i < len(rows):
        cells = rows[i].find_all("td", recursive=False)
        if not cells:
            i += 1
            continue
        seq += 1
        if cells[0].select_one(".F-trade") is not None:
            span = int(cells[0].get("rowspan") or 2)
            sides = []
            for k, row in enumerate(rows[i:i + span]):
                tds = row.find_all("td", recursive=False)[1 if k == 0 else 0:]
                players = [p for p in (_tx_player(b) for b in (tds[0].find_all("p") or [tds[0]])) if p] if tds else []
                team_id, team, stamp = _tx_team(tds[-1] if tds else None)
                sides.append((players, team_id, team, stamp))
            stamp = next((s[3] for s in sides if s[3]), "")
            day, time = _tx_date(stamp, season)
            for k, (players, team_id, team, _) in enumerate(sides):
                origin = next((s for j, s in enumerate(sides) if j != k), ([], "", "", ""))
                for p in players:
                    out.append(dict(p, date=day, time=time, type="trade", team_id=team_id, team=team,
                                    detail="Traded", other_team_id=origin[1], other_team=origin[2], transaction=seq))
            i += span
            continue
        titles = [s.get("title", "") for s in cells[0].select("span[title]")]
        kinds = ["add" if "Added" in t else "drop" if "Dropped" in t else "" for t in titles]
        if any(kinds) and len(cells) >= 2:
            team_id, team, stamp = _tx_team(cells[-1])
            day, time = _tx_date(stamp, season)
            blocks = cells[1].select("div.Pbot-xs") or [cells[1]]
            for kind, block in zip(kinds, blocks):
                p = _tx_player(block)
                if not kind or p is None:
                    continue
                note = block.select_one("h6")
                out.append(dict(p, date=day, time=time, type=kind, team_id=team_id, team=team,
                                detail=re.sub(r"\s+", " ", note.get_text(" ", strip=True)) if note else "",
                                other_team_id="", other_team="", transaction=seq))
        i += 1
    return out, has_next


def transactions(league_id, season, timeout=30, session=None, max_pages=TX_MAX_PAGES):
    """Toutes les transactions de la saison (page « All Transactions », 25 par page, de la plus récente)."""
    out, seen = [], set()
    for page in range(max_pages):
        html = fetch(f"transactions?transactionsfilter=all&count={page * TX_PAGE}", league_id, timeout, session,
                     season=season)
        rows, has_next = parse_transactions(html, season)
        offset = len(out)
        for r in rows:
            r["transaction"] = f"{page}-{r['transaction']}"
            key = (r["date"], r["time"], r["type"], r["player_id"], r["team_id"])
            if key not in seen:
                seen.add(key)
                out.append(r)
        if not rows or not has_next or len(out) == offset:
            break
    return out
