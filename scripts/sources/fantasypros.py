"""Import des projections FantasyPros (page « overall », tous postes)."""

import logging
import re

from bs4 import BeautifulSoup

from scripts.sources.base import ProjectionSource

log = logging.getLogger(__name__)

# "(OKC - PG,SG)" -> équipe "OKC", positions ["PG", "SG"]
_TEAM_POS = re.compile(r"\(\s*([A-Z]{2,4})\s*-\s*([^)]*)\)")


class FantasyProsSource(ProjectionSource):
    name = "fantasypros"
    label = "FantasyPros"

    def page_requests(self):
        return [(self.url_template(), None)]

    def parse_page(self, html, _context):
        soup = BeautifulSoup(html, "lxml")
        table = soup.find("table", {"id": "data"})
        if table is None or table.find("tbody") is None:
            return []

        if "not available" in table.find("tbody").get_text(" ", strip=True).lower():
            log.warning("[FantasyPros] Projections pas encore publiées pour l'étape '%s'.", self.stage)
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
            rows.append({"player": player, "team": team, "positions": positions, "stats": stats})
        return rows


def fetch_fantasypros_projections(settings=None):
    return FantasyProsSource(settings).run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from scripts.db import init_db

    init_db()
    fetch_fantasypros_projections()
