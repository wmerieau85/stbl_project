"""Vérifie que les sites utilisés répondent depuis la machine courante (ex. serveurs GitHub)."""
import sys
import time

import cloudscraper
import requests

URLS = {
    "cbs ros": "https://www.cbssports.com/fantasy/basketball/stats/C/2027/restofseason/projections/",
    "fantasypros ros": "https://www.fantasypros.com/nba/projections/ros-overall.php",
    "fantasypros L15": "https://www.fantasypros.com/nba/stats/overall.php?days=15",
    "9cat (365scores)": "https://api.fantasy.365scores.com/sports/players/stats?competitions=103&period=projected",
    "espn": "https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/2027/segments/0/leaguedefaults/1?view=kona_player_info",
    "rotoballer": "https://www.rotoballer.com/wp-json/rb/v1/sheet-rankings?sport=nba&format=9cat",
    "nba.com calendrier": "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json",
    "fixturedownload": "https://fixturedownload.com/feed/json/nba-2026",
    "yahoo standings": "https://basketball.fantasysports.yahoo.com/2026/nba/4205",
    "yahoo draftresults": "https://basketball.fantasysports.yahoo.com/2026/nba/4205/draftresults",
    "yahoo roster": "https://basketball.fantasysports.yahoo.com/2026/nba/4205/1",
}
ok = True
scraper = cloudscraper.create_scraper()
for name, url in URLS.items():
    for label, client in (("requests", requests), ("cloudscraper", scraper)):
        t = time.time()
        try:
            r = client.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            print(f"{name:<20} {label:<12} {r.status_code} {len(r.content):>8} o {time.time() - t:4.1f} s")
            if r.status_code == 200 and len(r.content) > 1000:
                break
        except Exception as exc:
            print(f"{name:<20} {label:<12} ERREUR {type(exc).__name__}: {str(exc)[:80]}")
    else:
        ok = False
sys.exit(0)
