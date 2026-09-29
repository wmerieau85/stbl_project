"""Calendrier NBA de la saison : {date: {codes équipes qui jouent}}.

Sources, dans l'ordre : NBA.com (JSON officiel), puis fixturedownload.com. Le calendrier est
gardé en cache une journée dans data/cache/ (les horaires changent peu).
"""

import json
import logging
import os
import time
from datetime import date, datetime, timedelta

import requests

from scripts.config import BASE_DIR

log = logging.getLogger(__name__)

CACHE_DIR = os.path.join(BASE_DIR, "data", "cache")
NBA_URL = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json"
FIXTURE_URL = "https://fixturedownload.com/feed/json/nba-{year}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0 Safari/537.36",
           "Referer": "https://www.nba.com/"}

TEAM_CODES = {
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN", "Charlotte Hornets": "CHA",
    "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE", "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN",
    "Detroit Pistons": "DET", "Golden State Warriors": "GSW", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "LA Clippers": "LAC", "Los Angeles Clippers": "LAC", "Los Angeles Lakers": "LAL", "Memphis Grizzlies": "MEM",
    "Miami Heat": "MIA", "Milwaukee Bucks": "MIL", "Minnesota Timberwolves": "MIN", "New Orleans Pelicans": "NOP",
    "New York Knicks": "NYK", "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL", "Philadelphia 76ers": "PHI",
    "Phoenix Suns": "PHX", "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC", "San Antonio Spurs": "SAS",
    "Toronto Raptors": "TOR", "Utah Jazz": "UTA", "Washington Wizards": "WAS",
}
# Yahoo / sites -> code NBA
ALIASES = {"GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "PHO": "PHX", "WSH": "WAS", "BRK": "BKN",
           "CHO": "CHA", "UTAH": "UTA", "NOR": "NOP"}


def team_code(code):
    code = (code or "").strip().upper()
    return ALIASES.get(code, code)


def _from_nba(timeout):
    data = requests.get(NBA_URL, headers=HEADERS, timeout=timeout).json()
    games = []
    for day in data["leagueSchedule"]["gameDates"]:
        for g in day["games"]:
            if not str(g.get("gameId", "")).startswith("002"):   # saison régulière seulement
                continue
            d = (g.get("gameDateEst") or "")[:10]
            games.append([d, g["homeTeam"]["teamTricode"], g["awayTeam"]["teamTricode"]])
    return games


def _from_fixture(start_year, timeout):
    data = requests.get(FIXTURE_URL.format(year=start_year), headers=HEADERS, timeout=timeout).json()
    games = []
    for g in data:
        home, away = TEAM_CODES.get(g.get("HomeTeam")), TEAM_CODES.get(g.get("AwayTeam"))
        if not home or not away:
            continue   # matchs pas encore attribués (NBA Cup)
        utc = datetime.strptime(g["DateUtc"][:16], "%Y-%m-%d %H:%M")
        games.append([(utc - timedelta(hours=6)).date().isoformat(), home, away])   # ~ heure US
    return games


def load_games(season, timeout=30, max_age_hours=24):
    """[[date ISO, domicile, extérieur], ...] de la saison régulière."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"schedule_{season}.json")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age_hours * 3600:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    start_year = int(season[:4])
    games = None
    for label, loader in (("NBA.com", lambda: _from_nba(timeout)),
                          ("fixturedownload", lambda: _from_fixture(start_year, timeout))):
        try:
            games = loader()
            if games and games[0][0][:4] in (str(start_year), str(start_year + 1)):
                log.info("[Calendrier] %d matchs lus (%s).", len(games), label)
                break
            games = None
        except (requests.RequestException, ValueError, KeyError) as exc:
            log.info("[Calendrier] %s indisponible (%s).", label, exc)
    if not games:
        if os.path.exists(path):
            log.warning("[Calendrier] Sources indisponibles : cache précédent utilisé.")
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        raise RuntimeError("Calendrier NBA introuvable (NBA.com et fixturedownload indisponibles).")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(games, fh)
    return games


def games_by_team(games, start, end=None):
    """{code équipe: nombre de matchs} entre start (inclus) et end (exclu, None = fin de saison)."""
    start = start.isoformat() if isinstance(start, date) else start
    end = end.isoformat() if isinstance(end, date) else end
    counts = {}
    for d, home, away in games:
        if d >= start and (end is None or d < end):
            counts[home] = counts.get(home, 0) + 1
            counts[away] = counts.get(away, 0) + 1
    return counts
