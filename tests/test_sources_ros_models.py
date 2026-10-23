"""Sources RoS RotoWire (via Sleeper), DARKO et Net Prophet : lecture des documents."""

import pytest

from scripts.sources.base import complete_stats, standardize_stats
from scripts.sources.darko import parse_darko
from scripts.sources.netprophet import parse_netprophet, remaining_share
from scripts.sources.rotowire import parse_rotowire
from scripts.config import load_default_config


def _std(source, stats):
    conf = load_default_config()["sources"][source]
    cols = {k.lower(): v for k, v in conf["columns"].items()}
    return complete_stats(standardize_stats(stats, cols, conf.get("percent_scale", 1)), per_game=True)


def test_rotowire_keeps_per_game_stats_without_games():
    data = [
        {"company": "rotowire", "team": "DEN", "stats": {"gp": 1.0, "pts": 27.9, "reb": 13.1, "fgm": 10.4,
                                                        "fga": 18.1, "tpm": 1.6, "tpa": 4.3, "to": 3.3},
         "player": {"first_name": "Nikola", "last_name": "Jokić", "fantasy_positions": ["C"]}},
        {"company": "rotowire", "stats": {}, "player": {"first_name": "Sans", "last_name": "Stats"}},
        {"company": "autre", "stats": {"pts": 1}, "player": {"first_name": "Autre", "last_name": "Source"}},
    ]
    rows = parse_rotowire(data)
    assert [r["player"] for r in rows] == ["Nikola Jokić"]
    stats = _std("rotowire", rows[0]["stats"])
    assert stats.get("gp") is None                      # gp = 1 chez Sleeper : pas un nombre de matchs
    assert stats["pts"] == 27.9 and stats["fg3m"] == 1.6 and stats["tov"] == 3.3
    assert stats["fgp"] == pytest.approx(10.4 / 18.1, abs=1e-4)


def _devalue(players, as_of="2026-10-22"):
    """Encode {"players": [...], "asOf": ...} au format SvelteKit (valeurs indexées)."""
    data = [None]
    def add(v):
        if isinstance(v, dict):
            i = len(data); data.append(None)
            data[i] = {k: add(x) for k, x in v.items()}
            return i
        if isinstance(v, list):
            i = len(data); data.append(None)
            data[i] = [add(x) for x in v]
            return i
        data.append(v)
        return len(data) - 1
    root = {"players": add(players), "asOf": add(as_of)}
    data[0] = root
    return {"type": "data", "nodes": [None, {"type": "data", "data": data}]}


def test_darko_converts_per_100_to_per_game():
    jokic = {"player_name": "Nikola Jokic", "team_name": "Denver Nuggets", "position": "C-F",
             "x_minutes": 32.6348, "x_pace": 98.2786, "x_pts_100": 32.5631, "x_ast_100": 13.972,
             "x_orb_100": 3.42759, "x_drb_100": 12.9197, "x_stl_100": 2.10595, "x_blk_100": 1.0746,
             "x_tov_100": 5.15993, "x_fga_100": 22.4874, "x_fg3a_100": 5.40999, "x_fta_100": 8.89366,
             "x_fg_pct": 0.541963, "x_fg3_pct": 0.354288, "x_ft_pct": 0.814659}
    rows = parse_darko(_devalue([jokic, {"player_name": "Sans minutes", "x_minutes": 0, "x_pace": 98}]))
    assert len(rows) == 1
    row = rows[0]
    assert row["team"] == "DEN" and row["positions"] == ["C", "F"]
    stats = _std("darko", row["stats"])
    assert stats["pts"] == pytest.approx(21.8, abs=0.05)            # valeur affichée sur darko.app
    assert stats["reb"] == pytest.approx(10.9, abs=0.05)
    assert stats["mpg"] == pytest.approx(32.63, abs=0.01) and stats.get("gp") is None
    assert stats["fgm"] == pytest.approx(stats["fga"] * 0.542, abs=0.01)
    assert stats["fg3m"] == pytest.approx(stats["fg3a"] * 0.3543, abs=0.01)


RANKINGS = """<table><thead><tr><th>#</th><th>Player</th><th>Team</th><th>Pos</th><th>PTS</th><th>REB</th>
<th>AST</th><th>STL</th><th>BLK</th><th>3PM</th><th>TO</th><th>FG%</th><th>FT%</th></tr></thead><tbody>
<tr><td>2</td><td>Nikola Jokic</td><td>DEN</td><td>C</td><td>26.4</td><td>11.8</td><td>9.2</td><td>1.5</td>
<td>0.7</td><td>1.7</td><td>3.4</td><td>57.3%</td><td>80.5%</td></tr>
<tr><td>92</td><td>AJ Dybantsa</td><td>WAS</td><td>PF/SF</td><td>16.8</td><td>5.8</td><td>2.9</td><td>0.9</td>
<td>0.5</td><td>1.3</td><td>2.2</td><td>43.2%</td><td>71.5%</td></tr></tbody></table>"""
GAMES = """<table><thead><tr><th>#</th><th>Player</th><th>Team</th><th>Pos</th><th>Projected GP 2026-27 (mean)</th>
<th>GP 2025-26</th><th>Board rank</th></tr></thead><tbody>
<tr><td>3</td><td>AJ Dybantsa</td><td>WAS</td><td>PF/SF</td><td>68</td><td>0</td><td>92</td></tr>
<tr><td>9</td><td>Nikola Jokic</td><td>DEN</td><td>C</td><td>62</td><td>65</td><td>2</td></tr></tbody></table>"""


def test_netprophet_scales_games_to_the_rest_of_season_and_skips_rookies():
    rows = {r["player"]: r for r in parse_netprophet(RANKINGS, GAMES, {"DEN": 0.75, "WAS": 0.75})}
    jokic = _std("netprophet", rows["Nikola Jokic"]["stats"])
    assert jokic["gp"] == pytest.approx(46.5)                    # 62 matchs sur la saison x 75 % restants
    assert jokic["pts"] == pytest.approx(26.4 * 46.5, abs=0.1)   # moyennes par match x matchs restants
    assert jokic["fgp"] == pytest.approx(0.573) and jokic["fg3m"] == pytest.approx(1.7 * 46.5, abs=0.1)
    rookie = _std("netprophet", rows["AJ Dybantsa"]["stats"])
    assert rookie.get("gp") is None and rookie["pts"] == 16.8    # rookie : valeur par défaut ignorée
    assert rows["AJ Dybantsa"]["positions"] == ["PF", "SF"]
    # sans calendrier ni tableau des matchs : stats seules
    assert all(r["stats"].get("gp") is None for r in parse_netprophet(RANKINGS, GAMES, None))
    assert all(r["stats"].get("gp") is None for r in parse_netprophet(RANKINGS, None, {"DEN": 0.75}))


def test_remaining_share_uses_the_schedule(monkeypatch):
    import scripts.season.schedule as sched
    games = [["2026-10-20", "DEN", "WAS"], ["2026-11-02", "DEN", "BOS"], ["2026-11-05", "WAS", "DEN"],
             ["2026-11-06", "BOS", "WAS"]]
    monkeypatch.setattr(sched, "load_games", lambda season: games)
    share = remaining_share("2026-27", today=__import__("datetime").date(2026, 10, 23))
    assert share["DEN"] == pytest.approx(2 / 3) and share["WAS"] == pytest.approx(2 / 3) and share["BOS"] == 1.0
