from scripts import db
from scripts.yahoo import rankings


def _player(key, name, team, pos, adp, pct="1.00"):
    return {"player": [
        [{"player_key": key}, {"player_id": key.split(".")[-1]}, {"name": {"full": name, "first": "", "last": ""}},
         {"editorial_team_abbr": team}, [], {"display_position": pos}],
        {"draft_analysis": [{"average_pick": adp}, {"average_round": "1.0"}, {"average_cost": "-"},
                            {"percent_drafted": pct}]},
    ]}


def _page(players):
    content = {str(i): p for i, p in enumerate(players)}
    content["count"] = len(players)
    return {"league": [{"league_key": "466.l.4205"}, {"players": content}]}


class FakeClient:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get(self, resource):
        self.calls.append(resource)
        return self.pages.pop(0) if self.pages else _page([])


def test_parse_and_paging():
    first = [_player(f"466.p.{i}", f"Joueur {i}", "den", "C", str(i + 0.5)) for i in range(25)]
    second = [_player("466.p.99", "Nikola Jokić", "Den", "C", "-", "-")]
    client = FakeClient([_page(first), _page(second)])
    rows = rankings.fetch_rankings(client, "466.l.4205", count=300)
    assert len(rows) == 26 and len(client.calls) == 2
    assert "sort=OR;start=25;count=25/draft_analysis" in client.calls[1]
    assert rows[0] == {"rank": 1, "player_key": "466.p.0", "player": "Joueur 0", "nba_team": "DEN", "positions": "C",
                       "adp": 0.5, "avg_round": 1.0, "pct_drafted": 1.0, "avg_cost": None}
    assert rows[25]["rank"] == 26 and rows[25]["adp"] is None


def test_save_links_players(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setattr(rankings, "load_aliases", lambda: {"Nicolas Claxton": "Nic Claxton"})
    db.init_db()
    with db.get_connection() as conn, conn:
        conn.execute("INSERT INTO players (player_id, canonical_name, name_key) VALUES (7, 'Nikola Jokic', 'nikola jokic')")
        conn.execute("INSERT INTO players (player_id, canonical_name, name_key) VALUES (8, 'Nic Claxton', 'nic claxton')")
    rows = rankings.parse_players(_page([_player("466.p.1", "Nikola Jokić", "DEN", "C", "1.4"),
                                         _player("466.p.2", "Nicolas Claxton", "BKN", "C", "90.2"),
                                         _player("466.p.3", "Inconnu", "BKN", "C", "-")]))
    assert rankings.save_rankings(rows, "2026-27") == ["Inconnu"]
    found = rankings.rankings_by_player("2026-27")
    assert found[7]["rank"] == 1 and found[7]["adp"] == 1.4 and found[8]["rank"] == 2
