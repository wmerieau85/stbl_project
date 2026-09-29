"""Import FantasyPros (page « overall », tous postes).

- étapes draft / ros : projections ;
- étapes sea / l30 / l15 / l07 : stats réelles (saison, 30, 15, 7 derniers jours), en totaux.
  La page ne donne ni FGM/FGA ni FTA : FGM = 2PM + 3PM, FGA = FGM / FG%, FTA = FTM / FT%.
"""

import logging
import re

from bs4 import BeautifulSoup

from scripts.sources.base import ProjectionSource, parse_number

log = logging.getLogger(__name__)

# "(OKC - PG,SG)" -> équipe "OKC", positions ["PG", "SG"]
_SEASON = re.compile(r"(20\d\d-\d\d)")
_TEAM_POS = re.compile(r"\(\s*([A-Z]{2,4})\s*-\s*([^)]*)\)")


class FantasyProsSource(ProjectionSource):
    name = "fantasypros"
    label = "FantasyPros"

    def page_requests(self):
        return [(self.url_template(), None)]

    def parse_page(self, html, _context):
        soup = BeautifulSoup(html, "lxml")
        if self.stage not in ("draft", "ros"):
            title = soup.find("title").get_text(" ", strip=True) if soup.find("title") else ""
            season = _SEASON.search(title) or _SEASON.search(soup.get_text(" ", strip=True)[:3000])
            if season and season.group(1) != self.season:
                log.warning("[FantasyPros] Stats '%s' de la saison %s (pas encore de %s) : ignorées.",
                            self.stage, season.group(1), self.season)
                return []
        table = soup.find("table", {"id": "data"})
        if table is None or table.find("tbody") is None:
            return []

        if "not available" in table.find("tbody").get_text(" ", strip=True).lower():
            log.warning("[FantasyPros] Données pas encore publiées pour l'étape '%s'.", self.stage)
            return []

        headers = [th.get_text(" ", strip=True).lower() for th in table.find("thead").find_all("th")]
        rows = []
        for tr in table.find("tbody").find_all("tr"):
            cells = tr.find_all("td")
            if len(cells) != len(headers):
                continue
            link = cells[0].find("a", class_="player-name") or cells[0].find("a")
            if link is None:
                continue
            player = link.get("fp-player-name") or link.get_text(strip=True)
            team, positions = "", []
            match = _TEAM_POS.search(cells[0].get_text(" ", strip=True))
            if match:
                team = match.group(1)
                positions = [p.strip() for p in match.group(2).split(",") if p.strip()]
            stats = {
                header: cell.get_text(strip=True)
                for header, cell in zip(headers[1:], cells[1:])
            }
            if "2pm" in stats and "fgm" not in stats:
                two, three = parse_number(stats.get("2pm")), parse_number(stats.get("3pm"))
                if two is not None and three is not None:
                    stats["fgm"] = str(two + three)
            rows.append({"player": player, "team": team, "positions": positions, "stats": stats})
        return rows


def fetch_fantasypros_projections(settings=None):
    return FantasyProsSource(settings).run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from scripts.db import init_db

    init_db()
    fetch_fantasypros_projections()
