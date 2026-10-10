import pytest

from scripts.yahoo import public
from scripts.yahoo import __main__ as yahoo_main


TEAM_LOG_HTML = """
<table>
  <thead>
    <tr><th>Players</th><th>Field Goals</th><th>Free Throws</th><th>3PT</th><th>Miscellaneous</th></tr>
    <tr>
      <th></th><th>Name</th><th></th><th>GP*</th><th>FGM/A*</th><th>FG%</th><th>FTM/A*</th>
      <th>FT%</th><th>3PTM</th><th>PTS</th><th>REB</th><th>AST</th><th>ST</th><th>BLK</th><th>TO</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>arrow</td><td><a href="https://sports.yahoo.com/nba/players/4912">Jimmy Butler III</a></td>
      <td>icon</td><td>12</td><td>71/138</td><td>.514</td><td>78/91</td><td>.857</td>
      <td>14</td><td>234</td><td>69</td><td>54</td><td>19</td><td>4</td><td>16</td>
    </tr>
  </tbody>
</table>
"""


def test_parse_team_log_extracts_season_totals_and_player_id():
    assert public.parse_team_log(TEAM_LOG_HTML) == [{
        "player": "Jimmy Butler III", "player_id": "4912", "gp": 12.0, "fgm": 71.0, "fga": 138.0,
        "fgp": 0.514, "ftm": 78.0, "fta": 91.0, "ftp": 0.857, "fg3m": 14.0, "pts": 234.0,
        "reb": 69.0, "ast": 54.0, "stl": 19.0, "blk": 4.0, "tov": 16.0,
    }]


def test_team_log_uses_yahoo_season_route():
    calls = []

    class Response:
        status_code = 200
        url = "https://basketball.fantasysports.yahoo.com/2025/nba/24104/1/teamlog"
        text = TEAM_LOG_HTML

    class Session:
        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()

    rows = public.team_log("24104", 1, "2025-26", session=Session())

    assert len(rows) == 1
    assert calls[0][0] == Response.url


def test_historical_standings_request_uses_season_route():
    calls = []

    class Response:
        status_code = 200
        url = "https://basketball.fantasysports.yahoo.com/2025/nba/24104/standings?opt_out=1"
        text = "<html></html>"

    class Session:
        def get(self, url, **kwargs):
            calls.append(url)
            return Response()

    public.fetch("standings?opt_out=1", "24104", session=Session(), season="2025-26")

    assert calls == [Response.url]


def test_team_log_reports_when_yahoo_has_not_published_it(monkeypatch):
    monkeypatch.setattr(public, "_fetch_url", lambda *args, **kwargs: "<html></html>")
    with pytest.raises(public.PublicPageError, match="après la fin de la première semaine"):
        public.team_log("24104", 1, "2025-26")


def test_stats_command_writes_both_blocks_and_maps_manager(monkeypatch):
    settings = {"active_season": "2025-26", "http": {"timeout": 10}}
    league = {
        "yahoo": {"league_id": "24104", "teams": {"Manager A": "Team A"}},
        "google_sheets": {"draft_spreadsheet_id": "book", "yahoo_tab": "yahoo"},
    }
    writes = []
    monkeypatch.setattr(yahoo_main, "load_settings", lambda: settings)
    monkeypatch.setattr(yahoo_main, "load_league", lambda: league)
    monkeypatch.setattr(yahoo_main, "YahooClient", lambda _: object())
    monkeypatch.setattr(yahoo_main, "_sync_config", lambda _settings, current: current)
    monkeypatch.setattr(yahoo_main, "open_spreadsheet", lambda _spreadsheet_id, _settings: "book")
    monkeypatch.setattr(public, "teams", lambda *args, **kwargs: [{"team_id": 1, "name": "Team A"}])
    monkeypatch.setattr(public, "team_log", lambda *args, **kwargs: [{"player": "Player A"}])
    monkeypatch.setattr(public, "standings", lambda *args, **kwargs: {"Team A": {"rank": 1}})
    monkeypatch.setattr(yahoo_main.yahoo_sheets, "write_standings",
                        lambda book, tab, teams, standings: writes.append(("standings", book, tab, teams, standings)))
    monkeypatch.setattr(yahoo_main.yahoo_sheets, "write_team_log",
                        lambda book, tab, teams: writes.append(("team_log", book, tab, teams)))

    assert yahoo_main.main(["stats"]) == 0
    assert writes[0][0:3] == ("standings", "book", "yahoo")
    assert writes[0][3][0]["manager"] == "Manager A"
    assert writes[1][0:3] == ("team_log", "book", "yahoo")


def test_stats_command_writes_standings_before_team_log_is_published(monkeypatch, capsys):
    """Avant la fin de la 1re semaine : standings écrits, bloc Team Log vide, code retour 0."""
    settings = {"active_season": "2026-27", "http": {"timeout": 10}}
    league = {"yahoo": {"league_id": "4205", "teams": {"Manager A": "Team A", "Manager B": "Team B"}},
              "google_sheets": {"draft_spreadsheet_id": "book", "yahoo_tab": "yahoo"}}
    writes = []
    monkeypatch.setattr(yahoo_main, "load_settings", lambda: settings)
    monkeypatch.setattr(yahoo_main, "load_league", lambda: league)
    monkeypatch.setattr(yahoo_main, "YahooClient", lambda _: object())
    monkeypatch.setattr(yahoo_main, "_sync_config", lambda _settings, current: current)
    monkeypatch.setattr(yahoo_main, "open_spreadsheet", lambda _spreadsheet_id, _settings: "book")
    monkeypatch.setattr(public, "teams", lambda *a, **k: [{"team_id": 1, "name": "Team A"},
                                                          {"team_id": 2, "name": "Team B"}])
    monkeypatch.setattr(public, "_fetch_url", lambda *a, **k: "<html></html>")
    monkeypatch.setattr(public, "standings", lambda *a, **k: {"Team A": {"rank": None}})
    monkeypatch.setattr(yahoo_main.yahoo_sheets, "write_standings",
                        lambda book, tab, teams, standings: writes.append(("standings", teams)))
    monkeypatch.setattr(yahoo_main.yahoo_sheets, "write_team_log",
                        lambda book, tab, teams: writes.append(("team_log", teams)))

    assert yahoo_main.main(["stats"]) == 0
    assert [w[0] for w in writes] == ["standings", "team_log"]
    assert all(t["players"] == [] for t in writes[1][1])
    assert "Team Log de 0 équipe(s)" in capsys.readouterr().out


def test_stats_command_still_fails_on_other_page_errors(monkeypatch):
    settings = {"active_season": "2026-27", "http": {"timeout": 10}}
    league = {"yahoo": {"league_id": "4205", "teams": {}}, "google_sheets": {"draft_spreadsheet_id": "book"}}
    monkeypatch.setattr(yahoo_main, "load_settings", lambda: settings)
    monkeypatch.setattr(yahoo_main, "load_league", lambda: league)
    monkeypatch.setattr(yahoo_main, "YahooClient", lambda _: object())
    monkeypatch.setattr(yahoo_main, "_sync_config", lambda _settings, current: current)
    monkeypatch.setattr(yahoo_main, "open_spreadsheet", lambda _spreadsheet_id, _settings: "book")
    monkeypatch.setattr(public, "teams", lambda *a, **k: [{"team_id": 1, "name": "Team A"}])

    def boom(*a, **k):
        raise public.PublicPageError("demande une connexion")

    monkeypatch.setattr(public, "team_log", boom)
    assert yahoo_main.main(["stats"]) == 1
