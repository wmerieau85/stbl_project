"""Page vide ou en échec : une nouvelle tentative avant d'abandonner la source."""
from scripts.sources import base


class FakeHttp:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get_text(self, url, headers=None):
        self.calls.append(url)
        return self.pages[url].pop(0)


class WebSource(base.ProjectionSource):
    name, label, per_game = "test", "Test", True

    def __init__(self, http, urls):
        self.http, self.urls = http, urls
        self.season, self.stage = "2026-27", "ros"
        self.columns_map, self.percent_scale = {"pts": "pts", "gp": "gp"}, 1

    def page_requests(self):
        return [(u, None) for u in self.urls]

    def parse_page(self, text, context):
        return [{"player": name, "team": "OKC", "positions": ["G"], "stats": {"gp": 10, "pts": 20}}
                for name in text.split(",") if name]


def test_empty_page_is_fetched_again(monkeypatch):
    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    http = FakeHttp({"pg": ["Alpha"], "sf": ["", "Beta,Gamma"]})     # page SF vide au premier passage
    records = WebSource(http, ["pg", "sf"]).fetch()
    assert sorted(r["player"] for r in records) == ["Alpha", "Beta", "Gamma"]
    assert http.calls == ["pg", "sf", "sf"]


def test_still_empty_after_retry_cancels_import(monkeypatch):
    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    http = FakeHttp({"pg": ["Alpha"], "sf": ["", None]})
    assert WebSource(http, ["pg", "sf"]).fetch() is None


def test_sources_with_own_documents_are_not_refetched(monkeypatch):
    class FileSource(WebSource):
        def documents(self):
            return [("fichier.csv", "", None)]

    monkeypatch.setattr(base.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("pas d'attente")))
    assert FileSource(FakeHttp({}), []).fetch() is None
