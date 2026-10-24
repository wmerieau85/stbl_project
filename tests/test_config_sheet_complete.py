from scripts import config_sheet as C


class FakeWS:
    def __init__(self, rows):
        self.rows = [list(r) for r in rows]

    def get(self, rng):
        return [list(r) for r in self.rows]

    def insert_rows(self, values, row, value_input_option=None):
        self.rows[row - 1:row - 1] = [list(v) for v in values]

    def append_rows(self, values, value_input_option=None, table_range=None):
        self.rows += [list(v) for v in values]


class FakeBook:
    def __init__(self, ws):
        self.ws = ws

    def worksheet(self, tab):
        return self.ws


def _without(rows, *keys):
    keys = {(C._norm(s), C._norm(p)) for s, p in keys}
    return [r for r in rows if (C._norm(r[0]), C._norm(r[1])) not in keys]


def test_full_tab_has_nothing_missing():
    assert C.missing_rows(C.build_rows()) == []


def test_missing_params_are_listed_with_current_value():
    rows = _without(C.build_rows(), ("Optim | Season", "Opponent default rate"), ("Optim | Moves", "Auto shortlist"))
    missing = C.missing_rows(rows)
    assert [(r[0], r[1]) for r in missing] == [("Optim | Season", "Opponent default rate"),
                                                ("Optim | Moves", "Auto shortlist")]
    assert missing[0][2] == 0.95 and missing[0][3]


def test_alias_counts_as_present():
    rows = _without(C.build_rows(), ("Optim | Season", "Short-term horizon (days)")) + [["Settings | Days", "ST", "15", ""]]
    assert ("Optim | Season", "Short-term horizon (days)") not in [(r[0], r[1]) for r in C.missing_rows(rows)]


def test_complete_inserts_at_end_of_section_without_touching_other_rows(monkeypatch):
    full = C.build_rows()
    rows = _without(full, ("Optim | Season", "Opponent default rate"), ("Optim | Moves", "Auto shortlist"))
    rows = [r for r in rows if not r[0].startswith("Optim | History")]       # section entière absente
    before = [list(r) for r in rows]
    ws = FakeWS(rows)
    added = C.complete(FakeBook(ws), "settings")
    assert {(r[0], r[1]) for r in added} >= {("Optim | Season", "Opponent default rate"),
                                              ("Optim | Moves", "Auto shortlist")}
    # lignes d'origine conservées, dans le même ordre
    kept = [r for r in ws.rows if r in before]
    assert kept == before
    sections = [r[0] for r in ws.rows]
    i = sections.index("Optim | Season")
    j = max(k for k, s in enumerate(sections) if s == "Optim | Season")
    assert ws.rows[j][1] == "Opponent default rate" and all(s == "Optim | Season" for s in sections[i:j + 1])
    assert ws.rows[-1][0].startswith("Optim | History")                     # section absente : en fin d'onglet
    assert C.missing_rows(ws.rows) == []
    assert C.complete(FakeBook(ws), "settings") == []
