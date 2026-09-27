"""Import des projections CBS Sports (une page par poste)."""

import logging

from bs4 import BeautifulSoup

from scripts.sources.base import ProjectionSource

log = logging.getLogger(__name__)


class CbsSource(ProjectionSource):
    name = "cbs"
    label = "CBS"

    def page_requests(self):
        # "2026-27" -> "2026" : CBS identifie la saison par son année de début
        year = self.season.split("-")[0]
        template = self.url_template()
        return [
            (template.format(position=pos, year=year), pos)
            for pos in self.source_config.get("positions", ["PG", "SG", "SF", "PF", "C"])
        ]

    @staticmethod
    def _header_code(th):
        # L'en-tête contient le code ("gp") suivi d'une infobulle ("Games Played") :
        # on ne garde que le code, porté par le lien de tri.
        link = th.find("a")
        text = link.get_text(" ", strip=True) if link else th.get_text(" ", strip=True)
        return text.split(" ")[0].lower() if text else ""

    def parse_page(self, html, page_position):
        soup = BeautifulSoup(html, "lxml")
        table = soup.find("table")
        if table is None or table.find("tbody") is None:
            return []

        headers = [self._header_code(th) for th in table.find("thead").find_all("th")]
        rows = []
        for tr in table.find("tbody").find_all("tr"):
            cells = tr.find_all("td")
            if len(cells) != len(headers):
                continue
            # La cellule joueur contient une version courte ("L. Doncic") et une version
            # longue ("Luka Doncic") : on lit uniquement la longue.
            long_name = cells[0].select_one(".CellPlayerName--long")
            if long_name is None or long_name.find("a") is None:
                continue
            team = long_name.select_one(".CellPlayerName-team")
            stats = {
                header: cell.get_text(strip=True)
                for header, cell in zip(headers[1:], cells[1:])
            }
            rows.append({
                "player": long_name.find("a").get_text(strip=True),
                "team": team.get_text(strip=True) if team else "",
                # Position de la page consultée : un joueur présent sur plusieurs
                # pages cumule ses éligibilités (fusion dans ProjectionSource.fetch).
                "positions": [page_position],
                "stats": stats,
            })

        if len(rows) >= 100:
            log.debug("[CBS] %s : %d joueurs (CBS limite l'affichage à 100 par poste).", page_position, len(rows))
        return rows


def fetch_cbs_projections(settings=None):
    return CbsSource(settings).run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from scripts.db import init_db

    init_db()
    fetch_cbs_projections()
