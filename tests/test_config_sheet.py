import json

import pytest

from scripts import config_sheet as C
from scripts.config import load_bootstrap, load_config


def _cells(rows):
    return [[str(c) if c is not None else "" for c in r] for r in rows]


@pytest.fixture
def cfg():
    return load_config(), load_bootstrap()


def test_round_trip_is_lossless(cfg):
    config, boot = cfg
    rows = _cells(C.build_rows(config, boot))
    c2, b2 = C.parse_rows(rows, config, boot)
    assert (c2, b2) == (config, boot)
    assert set(c2["grids"]) >= {"draft", "lt", "st"}


def test_sheet_alone_rebuilds_sources_and_grids(cfg):
    config, boot = cfg
    rows = _cells(C.build_rows(config, boot))
    bare = {"league": {}, "settings": {}, "grids": {}, "aliases": {},
            "sources": {n: {"code": s["code"], ("urls" if "urls" in s else "files"): {}}
                        for n, s in config["sources"].items()}}
    c2, _ = C.parse_rows(rows, bare, boot)
    assert c2["sources"] == config["sources"]
    assert c2["grids"] == config["grids"]


def test_order_is_free_and_aliases_accepted(cfg):
    config, boot = cfg
    rows = _cells(C.build_rows(config, boot))[1:]
    rows.reverse()
    rows = [r for r in rows if r[:2] != ["Sheets | Season", "Rosters Source"]]
    rows.append(["Sheets | Season", "Choices Source", "sheet", ""])
    c2, _ = C.parse_rows(rows, config, boot)
    assert c2["league"]["draft"]["order"] == config["league"]["draft"]["order"]
    assert c2["league"]["season"]["roster_source"] == "sheet"


def test_per_stage_activation(cfg):
    config, boot = cfg
    rows = [r for r in _cells(C.build_rows(config, boot))
            if not (r[0] == "Sources | Active" and r[1] == "ros" and r[2] == "cbs")]
    c2, _ = C.parse_rows(rows, config, boot)
    assert c2["settings"]["sources"]["cbs"] == {"draft": True, "ros": False}


def test_wrong_manager_count_rejected(cfg):
    config, boot = cfg
    rows = [r for r in _cells(C.build_rows(config, boot)) if not (r[0] == "Draft | Order" and r[1] == "15")]
    with pytest.raises(ValueError):
        C.parse_rows(rows, config, boot)


def test_unknown_source_code_rejected(cfg):
    config, boot = cfg
    rows = _cells(C.build_rows(config, boot)) + [["Sources | RoS", "xx", "https://x", ""]]
    with pytest.raises(ValueError):
        C.parse_rows(rows, config, boot)


def test_invalid_grid_rejected(cfg):
    config, boot = cfg
    rows = _cells(C.build_rows(config, boot))
    i = next(i for i, r in enumerate(rows) if r[0] == "Grid lt | GP")
    rows[i][2] = "90,00%"
    with pytest.raises(ValueError):
        C.parse_rows(rows, json.loads(json.dumps(config)), boot)
