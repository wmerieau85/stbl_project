"""Onglet bdd : recopie des formules Team Draft / Team Season."""

from scripts import bdd


class _FilterBook:
    def __init__(self, basic_filter):
        self.basic_filter = basic_filter
        self.requests = []

    def worksheet(self, _tab):
        class Ws:
            id = 0
        return Ws()

    def fetch_sheet_metadata(self, _params=None):
        sheet = {"properties": {}}          # sheetId 0 absent de la réponse de l'API
        if self.basic_filter:
            sheet["basicFilter"] = self.basic_filter
        return {"sheets": [sheet]}

    def batch_update(self, body):
        self.requests.append(body["requests"])


def test_extend_formulas_without_filter_only_copies():
    book = _FilterBook(None)
    bdd.extend_formulas(book, {"projections_start_col": "C"}, "team_draft", 10)
    assert [list(r) for r in book.requests[0]] == [["copyPaste"]]


def test_extend_formulas_lifts_and_restores_filter_hiding_rows():
    flt = {"range": {"startRowIndex": 0, "endRowIndex": 1552, "startColumnIndex": 0, "endColumnIndex": 66},
           "criteria": {"0": {"hiddenValues": ["Arno"]}},
           "filterSpecs": [{"columnIndex": 0, "filterCriteria": {"hiddenValues": ["Arno"]}}]}
    book = _FilterBook(flt)
    bdd.extend_formulas(book, {"projections_start_col": "C"}, "team_draft", 1619)
    clear, copy, restore = book.requests[0]
    assert clear == {"clearBasicFilter": {"sheetId": 0}}
    assert copy["copyPaste"]["destination"]["endRowIndex"] == 1619
    new = restore["setBasicFilter"]["filter"]
    assert new["filterSpecs"] == flt["filterSpecs"] and "criteria" not in new
    assert new["range"] == {"sheetId": 0, "startRowIndex": 0, "endRowIndex": 1619,
                            "startColumnIndex": 0, "endColumnIndex": 66}
