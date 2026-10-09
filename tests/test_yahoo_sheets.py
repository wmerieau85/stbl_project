import pytest

from scripts.config import validate_league
from scripts.draft import __main__ as draft_main
from scripts.yahoo import sheets


def test_yahoo_extracts_write_to_independent_fixed_blocks(monkeypatch):
    writes = []
    monkeypatch.setattr(sheets, "write_block",
                        lambda book, tab, rows, first_col: writes.append((tab, rows, first_col)))

    sheets.write_draft(object(), "yahoo", [{
        "pick": 1, "round": 1, "pick_in_round": 1, "team": "Team A", "player": "Player A",
        "player_key": "nba.p.1",
    }], {"Team A": "Manager A"})
    sheets.write_rosters(object(), "yahoo", [{
        "team_id": 2, "team": "Team A", "manager": "Manager A",
        "players": [{"player": "Player A", "player_id": "1", "slot": "C"}],
    }])
    sheets.write_rankings(object(), "yahoo", [{
        "rank": 1, "player": "Player A", "player_key": "nba.p.1", "player_id": 1, "adp": 2.5,
    }], "2026-27")

    assert [write[2] for write in writes] == ["A", "L", "V"]
    assert writes[0][1][1][4:6] == ["Manager A", "Player A"]
    assert writes[1][1][1][:5] == [2, "Team A", "Manager A", "Player A", "1"]
    assert writes[2][1][1][:5] == ["2026-27", 1, "Player A", "nba.p.1", 1]


def test_push_projections_leaves_formula_columns_a_and_b_unwritten(monkeypatch):
    writes = []
    projections = [["Phase", "Player"], ["draft", "Player A"]]
    monkeypatch.setattr(draft_main, "load_league", lambda: {
        "google_sheets": {"projections_tab": "bdd", "projections_start_col": "C", "detail_tab": ""},
    })
    monkeypatch.setattr(draft_main, "load_settings", lambda: {"active_season": "2026-27"})
    monkeypatch.setattr(draft_main, "build_all_rows", lambda season, phases: (projections, ["draft"]))
    monkeypatch.setattr(draft_main, "open_spreadsheet", lambda spreadsheet_id, settings: object())
    monkeypatch.setattr(draft_main, "write_block",
                        lambda book, tab, rows, first_col: writes.append((tab, rows, first_col)))

    draft_main.push_projections()

    assert writes == [("bdd", projections, "C")]


@pytest.mark.parametrize(
    "start_col, valid",
    [
        ("A", False),
        ("B", False),
        ("C", True),
        ("D", True),
        ("Z", True),
        ("AA", True),
        (" c ", True),
        ("", False),
        ("3", False),
        (3, False),
        (None, False),
        (True, False),
    ],
)
def test_projections_start_column_validation(start_col, valid):
    league = {"google_sheets": {"projections_start_col": start_col}}
    if valid:
        validate_league(league)
    else:
        with pytest.raises(ValueError, match="projections_start_col|Google Sheets"):
            validate_league(league)


@pytest.mark.parametrize("google_sheets", [[], "", 3, True])
def test_invalid_google_sheets_mapping_rejected(google_sheets):
    with pytest.raises(ValueError, match="google_sheets"):
        validate_league({"google_sheets": google_sheets})


def test_validate_league_accepts_c_and_after():
    assert validate_league({"google_sheets": {"projections_start_col": "C"}})["google_sheets"]["projections_start_col"] == "C"
    assert validate_league({"google_sheets": {"projections_start_col": "D"}})["google_sheets"]["projections_start_col"] == "D"
    assert validate_league({"google_sheets": {"projections_start_col": "AA"}})["google_sheets"]["projections_start_col"] == "AA"
