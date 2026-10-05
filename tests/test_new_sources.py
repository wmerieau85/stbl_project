import json
import os

from scripts.http_client import safe_url
from scripts.sources.base import complete_stats, standardize_stats
from scripts.sources.fantasynerds import parse_fantasynerds
from scripts.sources.ninecat import parse_ninecat
from scripts.config import load_source_config

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


def _std(row, source, per_game):
    conf = load_source_config(source)
    cols = {k.lower(): v for k, v in conf["columns"].items()}
    return complete_stats(standardize_stats(row["stats"], cols, conf.get("percent_scale", 1)), per_game=per_game)


def test_ninecat_projected():
    rows = parse_ninecat(_load("ninecat_stats.json"), _load("ninecat_players.json"))
    luka = next(r for r in rows if r["player"] == "Luka Doncic")
    assert luka["team"] == "LAL" and luka["positions"] == ["PG", "SG"]
    s = _std(luka, "ninecat", per_game=True)
    assert s["gp"] == 67 and s["pts"] == round(32.1 * 67, 2) and s["fga"] == round(21.7 * 67, 2)
    assert s["fgp"] == round(s["fgm"] / s["fga"], 4) and s["mpg"] == 35.4
    wemby = _std(next(r for r in rows if r["player"] == "Victor Wembanyama"), "ninecat", per_game=True)
    assert wemby["blk"] == round(3.2 * 67, 2)


def test_ninecat_season_stats_ignored_before_season():
    assert parse_ninecat(_load("ninecat_stats.json"), _load("ninecat_players.json"), stage="sea") == []


def test_fantasynerds_totals():
    rows = parse_fantasynerds(_load("fantasynerds_draft.json"))
    harden = _std(rows[0], "fantasynerds", per_game=False)
    assert rows[0]["player"] == "James Harden" and harden["pts"] == 2158 and harden["gp"] == 68
    assert harden["fgp"] == 0.443 and harden.get("fga") is None


def test_api_key_hidden():
    assert safe_url("https://x/y?apikey=SECRET&a=1") == "https://x/y?apikey=***&a=1"
