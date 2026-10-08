"""Import des projections de draft ESPN Fantasy Basketball via son API publique."""

import json
import logging

from scripts.sources.base import ProjectionSource

log = logging.getLogger(__name__)

TEAM_CODES = {
    1: "ATL", 2: "BOS", 3: "NOP", 4: "CHI", 5: "CLE", 6: "DAL", 7: "DEN",
    8: "DET", 9: "GSW", 10: "HOU", 11: "IND", 12: "LAC", 13: "LAL", 14: "MIA",
    15: "MIL", 16: "MIN", 17: "BKN", 18: "NYK", 19: "ORL", 20: "PHI",
    21: "PHX", 22: "POR", 23: "SAC", 24: "SAS", 25: "OKC", 26: "UTA",
    27: "WAS", 28: "TOR", 29: "MEM", 30: "CHA",
}
POSITIONS = {1: "PG", 2: "SG", 3: "SF", 4: "PF", 5: "C", 6: "G", 7: "F"}
POSITION_SLOTS = {0: "PG", 1: "SG", 2: "SF", 3: "PF", 4: "C", 5: "G", 6: "F"}
PLAYER_LIMIT = 500


class ESPNSource(ProjectionSource):
    name = "espn"
    label = "ESPN"

    def documents(self):
        year = int(self.season.split("-")[0]) + 1
        url = self.url_template().format(year=year)
        filters = {
            "players": {
                "limit": PLAYER_LIMIT,
                "offset": 0,
                "sortDraftRanks": {"sortPriority": 1, "sortAsc": True, "value": "STANDARD"},
            }
        }
        headers = {"x-fantasy-filter": json.dumps(filters, separators=(",", ":"))}
        log.info("[%s] Téléchargement %s", self.label, url)
        return [(url, self.http.get_text(url, headers=headers), year)]

    def parse_page(self, text, season_id):
        return parse_espn(json.loads(text), season_id)


def parse_espn(data, season_id):
    rows = []
    for item in data.get("players") or []:
        player = item.get("player") or {}
        projected = next(
            (
                stat for stat in player.get("stats") or []
                if stat.get("seasonId") == season_id
                and stat.get("statSourceId") == 1
                and stat.get("statSplitTypeId") == 0
                and stat.get("stats")
            ),
            None,
        )
        if not projected or not player.get("fullName"):
            continue
        positions = {
            POSITION_SLOTS.get(slot)
            for slot in player.get("eligibleSlots") or []
            if slot in POSITION_SLOTS
        }
        default_position = POSITIONS.get(player.get("defaultPositionId"))
        if default_position:
            positions.add(default_position)
        rows.append({
            "player": player["fullName"],
            "team": TEAM_CODES.get(player.get("proTeamId"), ""),
            "positions": sorted(positions),
            "stats": projected["stats"],
        })
    return rows
