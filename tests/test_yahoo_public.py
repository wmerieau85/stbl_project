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


ROSTER_HTML = """<table><tbody>
<tr><td>G</td><td><div class="ysf-player-name"><a class="name" title="Kon Knueppel" data-ys-playerid="10450">K. Knueppel</a>
<span class="ysf-player-status Nowrap F-injury Fz-xxs">Q</span><span class="Fz-xxs">CHA - G,F</span></div></td></tr>
<tr class="bench"><td>BN</td><td><a class="name" title="Trey Murphy III" data-ys-playerid="6559">T. Murphy</a>
<span class="Fz-xxs">NOP - G,F</span></td></tr>
</tbody></table>"""


def test_roster_reads_team_and_positions_after_injury_status(monkeypatch):
    monkeypatch.setattr(public, "fetch", lambda *a, **k: ROSTER_HTML)
    players = public.roster("4205", 1)["players"]
    assert players[0] == {"player": "Kon Knueppel", "player_id": "10450", "nba_team": "CHA", "positions": "G,F",
                          "slot": "G", "status": "Q"}
    assert players[1]["nba_team"] == "NOP" and players[1]["slot"] == "BN"


def _yahoo_env(monkeypatch, tmp_path):
    settings = {"active_season": "2026-27", "http": {"timeout": 10}}
    league = {"yahoo": {"league_id": "4205", "teams": {"Manager A": "Team A"}},
              "google_sheets": {"draft_spreadsheet_id": "book", "yahoo_tab": "yahoo"}}
    writes = []
    monkeypatch.setattr(yahoo_main, "load_settings", lambda: settings)
    monkeypatch.setattr(yahoo_main, "load_league", lambda: league)
    monkeypatch.setattr(yahoo_main, "YahooClient", lambda _: object())
    monkeypatch.setattr(yahoo_main, "_sync_config", lambda _settings, current: current)
    monkeypatch.setattr(yahoo_main, "open_spreadsheet", lambda _spreadsheet_id, _settings: "book")
    monkeypatch.setattr(yahoo_main, "EXPORTS_DIR", str(tmp_path))
    from scripts import db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.sqlite"))
    return writes


def test_draft_command_falls_back_to_public_page_on_403(monkeypatch, tmp_path):
    writes = _yahoo_env(monkeypatch, tmp_path)

    def denied(*a, **k):
        raise yahoo_main.YahooError("Accès refusé par Yahoo (403).")

    monkeypatch.setattr(yahoo_main, "league_key", denied)
    monkeypatch.setattr(public, "draft_results", lambda *a, **k: [
        {"pick": 1, "round": 1, "pick_in_round": 1, "team": "Team A", "player": "Player A", "player_id": "1"},
        {"pick": 2, "round": 1, "pick_in_round": 2, "team": "Team B", "player": "", "player_id": ""}])
    monkeypatch.setattr(yahoo_main.yahoo_sheets, "write_draft",
                        lambda book, tab, picks, managers: writes.append((tab, picks, managers)))

    assert yahoo_main.main(["draft"]) == 0
    tab, picks, managers = writes[0]
    assert tab == "yahoo" and [p["player"] for p in picks] == ["Player A"]
    assert managers == {"Team A": "Manager A"}
    csv_text = (tmp_path / "yahoo_draft_2026-27.csv").read_text(encoding="utf-8-sig")
    assert "1;1;Team A;Manager A;Player A" in csv_text


def test_rosters_command_writes_block_and_csv(monkeypatch, tmp_path):
    writes = _yahoo_env(monkeypatch, tmp_path)
    monkeypatch.setattr(public, "teams", lambda *a, **k: [{"team_id": 1, "name": "Team A"}])
    monkeypatch.setattr(public, "roster", lambda *a, **k: {
        "players": [{"player": "Player A", "player_id": "1", "nba_team": "DEN", "positions": "C", "slot": "C",
                     "status": ""}],
        "games": {"C": {"played": 0.0, "remaining": 82.0, "max": 82.0}}})
    monkeypatch.setattr(yahoo_main.yahoo_sheets, "write_rosters",
                        lambda book, tab, teams: writes.append((tab, teams)))

    from scripts import db
    db.init_db()
    with db.get_connection() as conn, conn:
        conn.execute("INSERT INTO players (canonical_name, name_key) VALUES ('Player A.', 'player a')")
    assert yahoo_main.main(["rosters"]) == 0
    tab, teams = writes[0]
    assert teams[0]["players"][0]["player"] == "Player A." and teams[0]["players"][0]["yahoo_name"] == "Player A"
    assert tab == "yahoo" and teams[0]["manager"] == "Manager A"
    assert "1;Team A;Manager A;Player A.;1;DEN;C;C;;Player A" in (tmp_path / "yahoo_rosters_2026-27.csv").read_text(
        encoding="utf-8-sig")
