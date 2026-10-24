import pytest

from scripts.weighting.engine import COUNTING, RAW_FIELDS, compute_player

STAT_KEYS = list(COUNTING) + ["fgp", "ftp"]


def _slot(code, weight=0.5):
    return {"code": code, "weights": {"gp": weight, "min": weight, "stats": {k: weight for k in STAT_KEYS}}}


def _row(**kw):
    row = {k: None for k in RAW_FIELDS}
    row.update(kw)
    return row


LT = _row(gp=60, mpg=30, pts=1200, fga=900, fgm=450, fgp=0.5, fta=240, ftm=192, ftp=0.8)


def test_zero_of_zero_window_is_ignored_not_counted_as_zero_percent():
    # page de stats réelles : 0 lancer réussi, FT% « .000 », tentatives inconnues
    window = _row(gp=2, mpg=30, pts=40, fga=30, fgm=15, fgp=0.5, ftm=0, fta=None, ftp=0.0)
    out = compute_player([(_slot("lt"), LT), (_slot("fp.l07"), window)])
    assert out["ftp"] == pytest.approx(0.8)


def test_player_without_game_gives_no_percentage():
    window = _row(gp=0, mpg=None, pts=0, fga=None, fgm=0, fgp=0.0, ftm=0, fta=None, ftp=0.0)
    out = compute_player([(_slot("lt"), LT), (_slot("fp.l07"), window)])
    assert out["fgp"] == pytest.approx(0.5) and out["ftp"] == pytest.approx(0.8)


def test_percentages_weighted_by_attempts():
    # 1/1 aux lancers sur 2 matchs (0,5 tentative par match) contre 4 tentatives par match projetées
    window = _row(gp=2, mpg=30, pts=40, fga=30, fgm=15, fgp=0.5, ftm=1, fta=1, ftp=1.0)
    out = compute_player([(_slot("lt"), LT), (_slot("fp.l07"), window)])
    expected = (0.8 * 4 + 1.0 * 0.5) / (4 + 0.5)
    assert out["ftp"] == pytest.approx(expected)
    assert out["ftp"] < 0.83


def test_sources_without_attempts_keep_plain_weighted_mean():
    a = _row(gp=60, mpg=30, pts=1200, fgp=0.5, ftp=0.8)
    b = _row(gp=60, mpg=30, pts=1200, fgp=0.4, ftp=0.7)
    out = compute_player([(_slot("a", 0.75), a), (_slot("b", 0.25), b)])
    assert out["fgp"] == pytest.approx(0.475) and out["ftp"] == pytest.approx(0.775)
