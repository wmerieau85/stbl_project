"""Exports Yahoo vers les blocs fixes de l'onglet source."""

from scripts.sheets import write_block

DRAFT_START_COL = "A"
ROSTERS_START_COL = "L"
RANKINGS_START_COL = "V"

DRAFT_HEADER = [
    "pick", "round", "pick_in_round", "team", "manager", "player", "positions", "nba_team",
    "team_key", "player_key",
]
ROSTERS_HEADER = [
    "team_id", "team", "manager", "player", "player_id", "nba_team", "positions", "slot", "status",
]
RANKINGS_HEADER = [
    "season", "rank", "player", "player_key", "player_id", "nba_team", "positions", "adp",
    "avg_round", "pct_drafted", "avg_cost",
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
