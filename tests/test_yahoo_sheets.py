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
    sheets.write_standings(object(), "yahoo", [{
        "team": "Team A", "manager": "Manager A",
    }], {"Team A": {"rank": 1, "stats": {"fgp": 0.51}, "points": {"fgp": 12}}})
    sheets.write_team_log(object(), "yahoo", [{
        "team_id": 2, "team": "Team A", "manager": "Manager A",
        "players": [{"player": "Player A", "player_id": "1", "gp": 12, "fgm": 71, "fga": 138}],
    }])

    assert [write[2] for write in writes] == ["A", "L", "V", "AH", "AO"]
    assert writes[0][1][1][4:6] == ["Manager A", "Player A"]
    assert writes[1][1][1][:5] == [2, "Team A", "Manager A", "Player A", "1"]
    assert writes[2][1][1][:5] == ["2026-27", 1, "Player A", "nba.p.1", 1]
    assert writes[3][1][1] == [1, "Team A", "Manager A", "FG%", 0.51, 12]
    assert writes[4][1][1][:8] == [2, "Team A", "Manager A", "Player A", "1", 12, 71, 138]
    assert len(writes[4][1][0]) == len(sheets.TEAM_LOG_HEADER)


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


class _FakeWs:
    id = 7

    def __init__(self, formulas):
        self.formulas = formulas

    def get(self, rng, value_render_option=None):
        return self.formulas


class _FakeBook:
    def __init__(self, formulas):
        self.ws, self.requests = _FakeWs(formulas), []

    def worksheet(self, tab):
        return self.ws

    def batch_update(self, body):
        self.requests += body["requests"]


def _setup_push(monkeypatch, book, current_rows):
    writes = []
    projections = [["Phase", "Player"], ["draft", "Player A"], ["draft", "Player B"], ["lt", "Player A"]]
    monkeypatch.setattr(draft_main, "load_league", lambda: {
        "google_sheets": {"projections_tab": "bdd", "projections_start_col": "C", "detail_tab": ""}})
    monkeypatch.setattr(draft_main, "load_settings", lambda: {
        "active_season": "2026-27", "active_stage": "draft", "phases": {"draft": ["draft"], "ros": ["lt", "st"]}})
    monkeypatch.setattr(draft_main, "build_all_rows", lambda season, phases: (projections, ["draft", "lt"]))
    monkeypatch.setattr(draft_main, "open_spreadsheet", lambda spreadsheet_id, settings: book)
    monkeypatch.setattr(draft_main, "write_block",
                        lambda b, tab, rows, first_col: writes.append((first_col, rows)))
    from scripts import bdd
    monkeypatch.setattr(bdd, "read_rows", lambda b, gs: current_rows)
    return writes


def test_push_projections_keeps_team_draft_formulas_and_extends_them(monkeypatch, capsys):
    book = _FakeBook([['=XLOOKUP($D2;rosters!$C:$C;rosters!$A:$A)', ""]])
    writes = _setup_push(monkeypatch, book, [["Team Draft", "Team Season", "Phase", "Player"],
                                             ["Arno", "", "draft", "Player A"]])
    draft_main.push_projections()
    assert [w[0] for w in writes] == ["C"]          # ni A (formules) ni B (vide)
    req = book.requests[0]["copyPaste"]
    assert req["pasteType"] == "PASTE_FORMULA" and req["destination"]["endRowIndex"] == 4
    assert req["destination"]["startColumnIndex"] == 0
    out = capsys.readouterr().out
    assert "recalculées à l'étape draft : draft" in out and "recopiées telles quelles depuis la base : lt" in out


def test_push_projections_values_still_follow_their_player(monkeypatch):
    book = _FakeBook([["Arno", ""]])
    writes = _setup_push(monkeypatch, book, [["Team Draft", "Team Season", "Phase", "Player"],
                                             ["Arno", "", "draft", "Player A"]])
    draft_main.push_projections()
    assert writes[1] == ("A", [["Team Draft"], ["Arno"], [""], ["Arno"]]) and len(writes) == 2
    assert not book.requests


def test_write_team_draft_skips_formula_column():
    from scripts import bdd

    book = _FakeBook([['=XLOOKUP($D2;rosters!$C:$C;rosters!$A:$A)']])
    assert bdd.write_team_draft(book, {"projections_tab": "bdd"}, {"Player A": "Arno"}) == 0
