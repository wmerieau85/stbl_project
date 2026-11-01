from datetime import date

from scripts.weighting import minutes

TODAY = date(2026, 11, 1)
LEAGUE = {"minutes": {"enabled": True, "target": 240, "protected": 2, "rotation": 3, "deep_floor": 10,
                      "min_real_games": 3}}


def _games(n=10, team="OKC"):
    return [(f"2026-11-{d:02d}", team, "XXX") for d in range(1, n + 1)]


def _slot(code="fp.ros"):
    return {"code": code, "weights": {"gp": 0.5, "min": 0.5, "stats": {"pts": 0.5}}}


def _row(name, mpg, gp=10, team="OKC"):
    return {"player": name, "team": team, "gp": gp, "mpg": mpg, "min": gp * mpg, "pts": gp * mpg / 2,
            "fga": gp * mpg / 3, "fgp": 0.5}


def _team(*mpgs):
    return {i: _row(f"P{i}", m) for i, m in enumerate(mpgs, 1)}


def _per_game(data, n=10):
    return sum(r["min"] for r in data.values()) / n


def test_surplus_taken_from_deep_bench_first():
    # rang = MPG de consensus : P1-P2 protégés, P3 palier 8e-10e, P4-P8 banc profond ; total 300 / match
    data = _team(60, 50, 40, 36, 34, 30, 30, 20)
    report = minutes.normalize([(_slot(), data)], "lt", LEAGUE, _games(), today=TODAY)
    assert round(_per_game(data)) == 240
    assert (data[1]["mpg"], data[2]["mpg"], data[3]["mpg"]) == (60, 50, 40)    # banc profond suffit
    assert round(data[4]["mpg"], 3) == round(36 * 90 / 150, 3)                  # banc 150 -> 90
    assert round(data[4]["pts"] / data[4]["min"], 9) == 0.5 and data[4]["gp"] == 10   # stats / minute, GP intacts
    assert data[4]["fgp"] == 0.5
    assert report["fp.ros"] == [("OKC", 300.0, 240.0)]


def test_deep_floor_then_rotation_players():
    data = _team(100, 100, 40, 15, 15)            # 270 / match ; banc profond (30) ramené au plancher 10
    minutes.normalize([(_slot(), data)], "lt", LEAGUE, _games(), today=TODAY)
    assert round(data[4]["mpg"] + data[5]["mpg"]) == 10
    assert round(data[3]["mpg"]) == 30            # reste du surplus (10) pris au 3e
    assert data[1]["mpg"] == 100


def test_never_below_real_mpg_and_role_from_real_minutes():
    data = _team(60, 50, 40, 36, 34, 30, 30, 20)
    # P8 : 20 projetées mais 45 réelles -> 3e (palier 8e-10e, épargné) au lieu du banc ; P4 jamais sous ses 36 réelles
    real = {8: 45.0, 4: 36.0}
    minutes.normalize([(_slot(), data)], "lt", LEAGUE, _games(), real=real, today=TODAY)
    assert data[8]["mpg"] == 20 and data[1]["mpg"] == 60
    assert data[4]["mpg"] == 36
    assert data[5]["mpg"] < 34 and data[6]["mpg"] < 30            # surplus pris aux autres


def test_injured_minutes_counted_after_return_only():
    data = _team(36, 34, 30, 60, 50, 40)          # 250 / match dont 60 d'un joueur absent toute la saison
    minutes.normalize([(_slot(), data)], "lt", LEAGUE, _games(), availability={"p4": "season"}, today=TODAY)
    assert data[5]["mpg"] == 50 and data[6]["mpg"] == 40      # 190 comptées : rien à retirer


def test_under_target_untouched_and_disabled():
    data = _team(36, 34, 30, 30, 30, 30, 30)      # 220
    assert minutes.normalize([(_slot(), data)], "lt", LEAGUE, _games(), today=TODAY) == {}
    assert data[4]["mpg"] == 30
    big = _team(100, 100, 100)
    off = {"minutes": dict(LEAGUE["minutes"], enabled=False)}
    assert minutes.normalize([(_slot(), big)], "lt", off, _games(), today=TODAY) == {}
    assert minutes.normalize([(_slot(), big)], "lt", LEAGUE, [], today=TODAY) == {}       # sans calendrier


def test_source_without_gp_uses_consensus_and_free_agents_ignored():
    darko = {i: dict(r, gp=None, min=None) for i, r in _team(36, 34, 30, 60, 50, 40, 30, 20).items()}
    darko[9] = _row("Free Agent", 30, team="FA")
    fp = _team(36, 34, 30, 30, 30, 30, 25, 25)     # 240 : fournit les GP
    loaded = [(_slot("dr.ros"), darko), (_slot("fp.ros"), fp)]
    report = minutes.normalize(loaded, "lt", LEAGUE, _games(), today=TODAY)
    assert set(report) == {"dr.ros"}
    assert round(sum(r["mpg"] for i, r in darko.items() if i != 9)) == 240
    assert darko[9]["mpg"] == 30


def test_real_mpg_reads_season_stats(tmp_path, monkeypatch):
    from scripts import db

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.sqlite"))
    db.init_db()
    with db.get_connection() as conn, conn:
        conn.execute("INSERT INTO players (player_id, canonical_name, name_key) VALUES (1, 'a', 'a'), (2, 'b', 'b')")
        for pid, src, gp, mpg in ((1, "FantasyPros", 6, 30.0), (1, "9cat", 5, 28.0), (2, "FantasyPros", 2, 40.0)):
            conn.execute("INSERT INTO raw_projections (player_id, player, player_key, source, season, stage, gp, mpg) "
                         "VALUES (?, 'x', ?, ?, '2026-27', 'sea', ?, ?)", (pid, f"k{pid}{src}", src, gp, mpg))
        assert minutes.real_mpg(conn, "2026-27", 3) == {1: 30.0}
