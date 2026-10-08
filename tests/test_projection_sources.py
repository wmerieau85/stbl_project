import pytest

from scripts.config import load_source_config
from scripts.sources.base import complete_stats, standardize_stats
from scripts.sources.espn import TEAM_CODES, parse_espn
from scripts.sources.rotoballer import parse_rotoballer
from scripts.weighting.engine import compute_player
from scripts.weighting.weights import load_grid


def test_espn_projection_totals():
    rows = parse_espn(
        {
            "players": [
                {
                    "player": {
                        "fullName": "Nikola Jokic",
                        "defaultPositionId": 5,
                        "eligibleSlots": [4, 9],
                        "proTeamId": 7,
                        "stats": [
                            {"seasonId": 2026, "statSourceId": 1, "statSplitTypeId": 0, "stats": {"0": 1}},
                            {
                                "seasonId": 2027,
                                "statSourceId": 1,
                                "statSplitTypeId": 0,
                                "stats": {
                                    "0": 2034,
                                    "1": 58,
                                    "2": 115,
                                    "3": 727,
                                    "6": 914,
                                    "11": 245,
                                    "13": 762,
                                    "14": 1325,
                                    "15": 388,
                                    "16": 475,
                                    "17": 122,
                                    "18": 310,
                                    "19": 0.575,
                                    "20": 0.817,
                                    "28": 35.6,
                                    "42": 72,
                                },
                            },
                        ],
                    }
                }
            ]
        },
        2027,
    )

    row = rows[0]
    config = load_source_config("espn")
    columns = {key.lower(): value for key, value in config["columns"].items()}
    stats = complete_stats(standardize_stats(row["stats"], columns, config["percent_scale"]))

    assert row["player"] == "Nikola Jokic"
    assert row["team"] == "DEN" and row["positions"] == ["C"]
    assert stats["gp"] == 72 and stats["mpg"] == 35.6
    assert stats["pts"] == 2034 and stats["fg3m"] == 122
    assert stats["fgm"] == 762 and stats["fga"] == 1325
    assert stats["ftm"] == 388 and stats["fta"] == 475 and stats["fg3a"] == 310
    assert stats["fgp"] == 0.575 and stats["ftp"] == 0.817


@pytest.mark.parametrize(
    "team_id,team",
    [(26, "UTA"), (27, "WAS"), (28, "TOR"), (29, "MEM"), (30, "CHA")],
)
def test_espn_team_codes(team_id, team):
    rows = parse_espn(
        {
            "players": [
                {
                    "player": {
                        "fullName": "Test Player",
                        "proTeamId": team_id,
                        "stats": [
                            {
                                "seasonId": 2027,
                                "statSourceId": 1,
                                "statSplitTypeId": 0,
                                "stats": {"0": 100, "42": 10},
                            }
                        ],
                    }
                }
            ]
        },
        2027,
    )

    assert rows[0]["team"] == team


def test_rotoballer_projects_per_game_without_claiming_games():
    rows = parse_rotoballer(
        {
            "format": "9cat",
            "data": [
                {
                    "name": "Nikola Jokic",
                    "team": "DEN",
                    "position": "C",
                    "proj": {
                        "pts": "26.4",
                        "reb": "12.5",
                        "ast": "9.8",
                        "stl": "1.4",
                        "blk": "0.8",
                        "3pm": "1.7",
                        "fg_pct": "57.3",
                        "ft_pct": "82.2",
                        "to": "3.3",
                    },
                }
            ],
        }
    )
    config = load_source_config("rotoballer")
    columns = {key.lower(): value for key, value in config["columns"].items()}
    stats = complete_stats(standardize_stats(rows[0]["stats"], columns, config["percent_scale"]), per_game=True)

    assert rows[0]["positions"] == ["C"]
    assert stats.get("gp") is None and stats["pts"] == 26.4 and stats["reb"] == 12.5
    assert stats["fgp"] == 0.573 and stats["ftp"] == 0.822


def test_ninecat_totals_and_rotoballer_per_game_stats_combine_correctly():
    ninecat_categories = {"pts": 0.9, "reb": 0.9, "ast": 0.9, "stl": 0.9, "blk": 0.9,
                          "tov": 0.9, "fg3m": 0.9, "fga": 0.9, "fta": 0.9, "fg3a": 0.9,
                          "fgp": 0.9, "ftp": 0.9}
    rotoballer_categories = {**ninecat_categories, "pts": 0.1}
    fields = ("gp", "mpg", "min", "pts", "reb", "ast", "stl", "blk", "tov", "fg3m", "fg3a",
              "fga", "fgm", "fgp", "fta", "ftm", "ftp")
    ninecat_row = {field: None for field in fields}
    ninecat_row.update(gp=70, mpg=30, pts=1750)
    rotoballer_row = {field: None for field in fields}
    rotoballer_row.update(pts=25)
    result = compute_player(
        [
            (
                {"weights": {"gp": 1, "min": 1, "stats": ninecat_categories},
                 "allow_per_game_without_gp": True},
                ninecat_row,
            ),
            (
                {
                    "weights": {"gp": 0, "min": 0, "stats": rotoballer_categories},
                    "allow_per_game_without_gp": True,
                },
                rotoballer_row,
            ),
        ]
    )

    assert result["gp"] == 70 and result["gp_coverage"] == 1
    assert result["mpg"] == 30 and result["min_coverage"] == 1
    assert result["_per_game"]["pts"] == 25
    assert result["pts"] == 1750
    assert result["stats_coverage"] == 1


def test_draft_grid_assigns_requested_new_source_weights():
    slots = {slot["code"]: slot for slot in load_grid("draft", "2026-27")["slots"]}

    assert slots["rb26"]["weights"]["gp"] == 0
    assert slots["rb26"]["weights"]["stats"]["pts"] == 0.1
    assert slots["es26"]["weights"]["gp"] == pytest.approx(0.1667, abs=0.0001)
    assert slots["es26"]["weights"]["stats"]["pts"] == 0.1


def test_detail_per_game_source_without_games_uses_weighted_games():
    """bdd_detail : moyennes RotoBaller (sans GP) ramenées aux matchs de la projection pondérée."""
    from scripts.draft import detail_tab

    row = {"source": "RotoBaller", "gp": None, "pts": 26.4, "reb": 12.5, "fgp": 0.57}
    detail_tab._games_from_final(row, {"gp": 74.0})
    assert row["gp"] == 74.0 and round(row["pts"], 1) == 1953.6 and row["fgp"] == 0.57
    assert detail_tab._per_game(row, "pts") == 26.4
    assert "RotoBaller" in detail_tab._per_game_labels()
