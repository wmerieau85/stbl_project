import json

import pytest

from scripts import config_sheet as C
from scripts.config import LEAGUE_PATH, SETTINGS_PATH, SOURCES_PATH, _read_json


def _cells(rows):
    return [[str(c) if c is not None else "" for c in r] for r in rows]


@pytest.fixture
def files():
    return _read_json(LEAGUE_PATH), _read_json(SETTINGS_PATH), _read_json(SOURCES_PATH)


def test_round_trip_is_lossless(files):
    league, settings, sources = files
    rows = _cells(C.build_rows(league, settings, sources))
    l2, s2, src2, grids = C.parse_rows(rows, league, settings, sources)
    assert (l2, s2, src2) == (league, settings, sources)
    assert set(grids) >= {"draft", "lt", "st"}


def test_old_labels_are_migrated(files):
    league, settings, sources = files
    rows = _cells(C.build_rows(league, settings, sources, phases=[]))
    rows = [r for r in rows if not (r and r[:2] == ["Draft", "Onglet de la recommandation"])]
    rows.insert(1, ["Sheets", "Onglet des recommandations", "reco", ""])
    league2, _, _, _ = C.parse_rows(rows, league, settings, sources)
    assert league2["google_sheets"]["reco_tab"] == "draft_reco"


def test_wrong_manager_count_rejected(files):
    league, settings, sources = files
    rows = _cells(C.build_rows(league, settings, sources, phases=[]))
    rows = [r for r in rows if not (r and len(r) > 1 and r[1] == league["draft"]["order"][-1])]
    with pytest.raises(ValueError):
        C.parse_rows(rows, league, settings, sources)


def test_invalid_grid_rejected(files):
    league, settings, sources = files
    rows = _cells(C.build_rows(league, settings, sources, phases=["lt"]))
    i = next(i for i, r in enumerate(rows) if r and r[0] == "Grille lt")
    rows[i + 3][1] = "90,00%"
    with pytest.raises(ValueError):
        C.parse_rows(rows, json.loads(json.dumps(league)), settings, sources)
