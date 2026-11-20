"""Exports Yahoo vers les blocs fixes de l'onglet source."""

from scripts.sheets import write_block

DRAFT_START_COL = "A"
ROSTERS_START_COL = "L"
RANKINGS_START_COL = "V"
STANDINGS_START_COL = "AH"
TEAM_LOG_START_COL = "AO"
TRANSACTIONS_START_COL = "BJ"     # après le Team Log (AO:BH) et une colonne vide

DRAFT_HEADER = [
    "pick", "round", "pick_in_round", "team", "manager", "player", "positions", "nba_team",
    "team_key", "player_key", "yahoo_name",
]
ROSTERS_HEADER = [
    "team_id", "team", "manager", "player", "player_id", "nba_team", "positions", "slot", "status", "yahoo_name",
]
RANKINGS_HEADER = [
    "season", "rank", "player", "player_key", "player_id", "nba_team", "positions", "adp",
    "avg_round", "pct_drafted", "avg_cost",
]
STANDINGS_HEADER = ["rank", "team", "manager", "category", "stat", "points"]
TEAM_LOG_HEADER = [
    "team_id", "team", "manager", "player", "player_id", "GP", "FGM", "FGA", "FG%", "FTM", "FTA", "FT%",
    "3PTM", "PTS", "REB", "AST", "ST", "BLK", "TO", "yahoo_name",
]
STANDINGS_CATEGORIES = [
    ("fgp", "FG%"), ("ftp", "FT%"), ("fg3m", "3PTM"), ("pts", "PTS"), ("reb", "REB"),
    ("ast", "AST"), ("stl", "ST"), ("blk", "BLK"), ("tov", "TO"),
]


def write_draft(book, tab, picks, team_to_manager=None):
    """Remplace le bloc des choix Yahoo sans toucher aux autres extractions."""
    team_to_manager = team_to_manager or {}
    rows = [DRAFT_HEADER]
    for pick in picks:
        team = pick.get("team", "")
        rows.append([
            pick.get("pick", ""), pick.get("round", ""), pick.get("pick_in_round", ""),
            team, team_to_manager.get(team, ""), pick.get("player", ""), pick.get("positions", ""),
            pick.get("nba_team", ""), pick.get("team_key", ""), pick.get("player_key", pick.get("player_id", "")),
            pick.get("yahoo_name", pick.get("player", "")),
        ])
    write_block(book, tab, rows, first_col=DRAFT_START_COL)


def write_rosters(book, tab, teams):
    """Remplace le bloc des effectifs Yahoo actuels sans toucher aux autres extractions."""
    rows = [ROSTERS_HEADER]
    for team in teams:
        for player in team.get("players", []):
            rows.append([
                team.get("team_id", ""), team.get("team", ""), team.get("manager", ""),
                player.get("player", ""), player.get("player_id", ""), player.get("nba_team", ""),
                player.get("positions", ""), player.get("slot", ""), player.get("status", ""),
                player.get("yahoo_name", player.get("player", "")),
            ])
    write_block(book, tab, rows, first_col=ROSTERS_START_COL)


def write_rankings(book, tab, rankings, season):
    """Remplace le bloc des rankings Yahoo sans toucher aux autres extractions."""
    rows = [RANKINGS_HEADER]
    rows.extend([
        [
            season, row.get("rank", ""), row.get("player", ""), row.get("player_key", ""),
            row.get("player_id", ""), row.get("nba_team", ""), row.get("positions", ""),
            row.get("adp") if row.get("adp") is not None else "",
            row.get("avg_round") if row.get("avg_round") is not None else "",
            row.get("pct_drafted") if row.get("pct_drafted") is not None else "",
            row.get("avg_cost") if row.get("avg_cost") is not None else "",
        ]
        for row in rankings
    ])
    write_block(book, tab, rows, first_col=RANKINGS_START_COL)


def write_standings(book, tab, teams, standings):
    """Écrit une ligne par équipe et catégorie, avec la statistique et les points roto Yahoo."""
    rows = [STANDINGS_HEADER]
    for team in teams:
        result = standings.get(team.get("team", ""), {})
        stats, points = result.get("stats", {}), result.get("points", {})
        for category, label in STANDINGS_CATEGORIES:
            if category not in stats and category not in points:
                continue
            rows.append([
                result.get("rank", ""), team.get("team", ""), team.get("manager", ""), label,
                stats.get(category, ""), points.get(category, ""),
            ])
    write_block(book, tab, rows, first_col=STANDINGS_START_COL)


TRANSACTIONS_HEADER = [
    "date", "time", "type", "team_id", "team", "manager", "player", "player_id", "nba_team", "positions",
    "detail", "other_team", "other_manager", "transaction", "yahoo_name",
]


def write_transactions(book, tab, transactions, manager_of=None):
    """Bloc des transactions de la saison (add / drop / trade), de la plus récente à la plus ancienne."""
    manager_of = manager_of or {}

    def manager(team):
        return manager_of.get(str(team or "").strip().casefold(), "")

    rows = [TRANSACTIONS_HEADER]
    for t in transactions:
        rows.append([t.get("date", ""), t.get("time", ""), t.get("type", ""), t.get("team_id", ""),
                     t.get("team", ""), manager(t.get("team")), t.get("player", ""), t.get("player_id", ""),
                     t.get("nba_team", ""), t.get("positions", ""), t.get("detail", ""), t.get("other_team", ""),
                     manager(t.get("other_team")), t.get("transaction", ""), t.get("yahoo_name", "")])
    write_block(book, tab, rows, first_col=TRANSACTIONS_START_COL)


def write_team_log(book, tab, teams):
    """Écrit les cumuls de saison du Team Log, y compris les joueurs hors roster actuel."""
    rows = [TEAM_LOG_HEADER]
    for team in teams:
        for player in team.get("players", []):
            rows.append([
                team.get("team_id", ""), team.get("team", ""), team.get("manager", ""),
                player.get("player", ""), player.get("player_id", ""), player.get("gp", ""),
                player.get("fgm", ""), player.get("fga", ""), player.get("fgp", ""),
                player.get("ftm", ""), player.get("fta", ""), player.get("ftp", ""),
                player.get("fg3m", ""), player.get("pts", ""), player.get("reb", ""),
                player.get("ast", ""), player.get("stl", ""), player.get("blk", ""),
                player.get("tov", ""), player.get("yahoo_name", ""),
            ])
    write_block(book, tab, rows, first_col=TEAM_LOG_START_COL)
