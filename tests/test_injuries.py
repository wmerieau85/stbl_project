from datetime import date

import numpy as np

from scripts import injuries
from scripts.draft.pool import STATS, Player, Pool
from scripts.season.engine import project_team
from scripts.weighting.engine import apply_injuries

TODAY = date(2026, 11, 1)
LEAGUE = {"games": {"per_slot": 82}, "roster": {"G": 0, "F": 0, "C": 2, "UTIL": 0}}


def _inj(name, status, ret="", override=""):
    return injuries.Injury(player=name, nba_team="OKC", status=status, espn_return=ret, override=override)


def test_resolve_rules():
    assert injuries.resolve(_inj("A", "Out", "2027-03-02")) == "2027-03-02"
    assert injuries.resolve(_inj("A", "Day-To-Day", "2026-11-03")) == ""
    assert injuries.resolve(_inj("A", "Out")) == ""
    assert injuries.resolve(_inj("A", "Out For Season")) == "season"
    assert injuries.resolve(_inj("A", "Out", "2027-03-02", "15/01/2027")) == "2027-01-15"
    assert injuries.resolve(_inj("A", "Out", "2027-03-02", "saison")) == "season"
    assert injuries.resolve(_inj("A", "Out", "2027-03-02", "ok")) == ""


def test_merge_keeps_overrides_and_manual_rows():
    espn = [_inj("Jalen Williams", "Out", "2027-03-02"), _inj("Mark Williams", "Out", "2027-02-03")]
    sheet = [_inj("Jalen Williams", "Out", "2027-01-01", "season"), _inj("Joueur Manuel", "", "", "2026-12-01"),
             _inj("Ancien Blesse", "Out", "2026-10-20")]
    merged = {i.player: i for i in injuries.merge(espn, sheet)}
    assert set(merged) == {"Jalen Williams", "Mark Williams", "Joueur Manuel"}
    assert merged["Jalen Williams"].override == "season" and merged["Jalen Williams"].return_used == "season"
    assert merged["Mark Williams"].return_used == "2027-02-03"
    rows = injuries.to_rows(merged.values())
    assert rows[0] == injuries.HEADER and rows[1][0] == "Jalen Williams"     # fin de saison en premier
    back = injuries.from_rows(rows)
    assert {i.player: i.override for i in back}["Joueur Manuel"] == "2026-12-01"


def test_availability_ignores_past_returns():
    lst = injuries.merge([_inj("A B", "Out", "2026-10-25"), _inj("C D", "Out", "2026-11-20")], [])
    av = injuries.availability(lst, TODAY)
    assert av == {injuries.name_key("C D"): date(2026, 11, 20)}


def test_load_off_mode():
    assert injuries.load({"season": {"injuries": "off"}}, write=False) == {}


def _games():
    out = []
    for m, days in ((11, 30), (12, 31)):
        out += [(f"2026-{m:02d}-{d:02d}", "OKC", "LAL") for d in range(1, days + 1)]
    return out


def _player(idx, name, gp):
    return Player(idx=idx, player_id=idx, name=name, team="OKC", positions="C", gp=gp,
                  per_game=np.ones(len(STATS)), value_avg=1.0, value_tot=1.0, rank_tot=idx, adp=None, market=idx)


def test_project_team_waits_for_return_date():
    pool = Pool([_player(1, "Jalen Williams", 61), _player(2, "Out Season", 61)])
    roster = {"players": [{"player": n, "nba_team": "OKC", "positions": "C", "slot": "C"}
                          for n in ("Jalen Williams", "Out Season")], "games": {"C": {"remaining": 200}}}
    league = dict(LEAGUE, _injuries={"jalen williams": date(2026, 12, 1), "out season": "season"})
    team = project_team("Moi", "Equipe", roster, None, pool, None, _games(), TODAY, 15, league)
    jw, out = team.players
    assert jw.games_st_sched == 0 and jw.games_lt_sched == 31 and jw.out_until == "2026-12-01"
    assert jw.games_used == 31
    assert out.games_sched == 0 and out.games_used == 0 and out.out_until == "season"


def test_weighting_caps_gp_after_return():
    results = [{"player": "Jalen Williams", "team": "OKC", "gp": 61.0, "pts": 1220.0, "reb": 305.0, "min": 2000.0,
                "fga": 900.0, "fgm": None},
               {"player": "Out Season", "team": "OKC", "gp": 50.0, "pts": 500.0},
               {"player": "Healthy", "team": "OKC", "gp": 60.0, "pts": 600.0}]
    av = {"jalen williams": date(2026, 12, 1), "out season": "season"}
    n = apply_injuries(results, "lt", "2026-27", {}, {}, availability=av, games=_games(), today=TODAY)
    assert n == 2
    jw, out, ok = results
    assert jw["gp"] == 31 and abs(jw["pts"] - 620) < 1e-9 and abs(jw["reb"] - 155) < 1e-9
    assert out["gp"] == 0 and out["pts"] == 0
    assert ok["gp"] == 60 and ok["pts"] == 600


def test_weighting_out_for_season_all_zero():
    r = {"player": "Jalen Williams", "team": "OKC", "gp": 19.0, "mpg": 32.5, "min": 617.5, "pts": 380.0,
         "fga": 266.0, "fgp": 0.485, "ftp": 0.82, "fg3p": 0.35, "fgm": None}
    apply_injuries([r], "lt", "2026-27", {}, {}, availability={"jalen williams": "season"}, games=[], today=TODAY)
    assert all(r[c] == 0 for c in ("gp", "mpg", "min", "pts", "fga", "fgp", "ftp", "fg3p"))


def test_out_for_season_kept_in_st_phase(tmp_path, monkeypatch):
    """Phase st : GP repris de lt (0 pour un blessé jusqu'à la fin de saison) ; le joueur reste listé, à 0."""
    from scripts import db
    from scripts.weighting import engine

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setattr(engine, "export_csv", lambda *a, **k: None)
    monkeypatch.setattr(engine, "load_availability", lambda *a, **k: {"jalen williams": "season"})
    db.init_db()
    with db.get_connection() as conn, conn:
        engine.ensure_table(conn)
        for pid, name, gp in ((1, "Jalen Williams", 0.0), (2, "Healthy", 60.0)):
            conn.execute("INSERT INTO final_projections (phase, season, player_id, player, team, gp, mpg, pts, "
                         "fga, fgp) VALUES ('lt', '2026-27', ?, ?, 'OKC', ?, 30, ?, ?, 0.5)",
                         (pid, name, gp, gp * 20, gp * 15))
    assert engine.compute_phase("st", {"active_season": "2026-27", "active_stage": "ros"}) == 2
    with db.get_connection() as conn:
        row = conn.execute("SELECT gp, mpg, pts, fgp FROM final_projections WHERE phase='st' AND player_id=1").fetchone()
    assert row == (0, 0, 0, 0)


def test_availability_uses_reference_names():
    lst = injuries.merge([_inj("Nic Claxton", "Out", "2026-12-01"), _inj("Inconnu", "Out")], [])
    av = injuries.availability(lst, TODAY, {"Nic Claxton": "Nicolas Claxton"})
    assert av[injuries.name_key("Nicolas Claxton")] == date(2026, 12, 1)
    assert av[injuries.name_key("Nic Claxton")] == date(2026, 12, 1)


def test_canonical_map_applies_aliases(monkeypatch):
    from scripts import player_linker

    monkeypatch.setattr(player_linker, "canonical_names",
                        lambda names: {n: ("Nicolas Claxton" if n == "Nic Claxton" else None) for n in names})
    lst = injuries.merge([_inj("Nic Claxton", "Out", "2026-12-01"), _inj("Healthy Guy", "Day-To-Day")], [])
    assert injuries.canonical_map(lst) == {"Nic Claxton": "Nicolas Claxton"}
