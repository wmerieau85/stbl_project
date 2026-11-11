from scripts import config_sheet as C


class FakeWS:
    """Grille de la taille exacte des lignes (comme un onglet sans ligne vide en bas)."""

    def __init__(self, rows):
        self.rows = [list(r) for r in rows]

    @property
    def row_count(self):
        return len(self.rows)

    def get(self, rng):
        return [list(r) for r in self.rows]

    def insert_rows(self, values, row, value_input_option=None, inherit_from_before=False):
        # comme l'API Sheets : après la dernière ligne, seulement en héritant de la ligne précédente
        if row - 1 >= self.row_count and not inherit_from_before:
            raise ValueError("range.startIndex must be less than the grid size if inheritFromBefore is false")
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


def test_every_param_section_is_written():
    """Chaque réglage non avancé de PARAMS figure dans l'onglet reconstruit (sinon Compléter settings ne le
    propose jamais) ; un réglage avancé seulement s'il s'écarte de sa valeur par défaut."""
    built = {(C._norm(r[0]), C._norm(r[1])) for r in C.build_rows(C.load_default_config())[1:]}
    absent = [(sec, par) for sec, par, *_ in C.PARAMS if (C._norm(sec), C._norm(par)) not in built]
    assert set(absent) == C.ADVANCED


def test_advanced_param_written_when_changed_and_read_when_present():
    cfg = C.load_default_config()
    cfg["league"].setdefault("minutes", {})["max_cut"] = 0.4
    rows = C.build_rows(cfg)
    assert ["Optim | Minutes", "Max cut per player"] == [r[:2] for r in rows if r[1] == "Max cut per player"][0]
    parsed, _ = C.parse_rows(C.build_rows(C.load_default_config()) + [["Optim | Minutes", "Max cut per player", "0,35"]])
    assert parsed["league"]["minutes"]["max_cut"] == 0.35


def test_minutes_params_proposed_when_absent():
    rows = [r for r in C.build_rows() if C._norm(r[0]) != C._norm("Optim | Minutes")]
    missing = [(r[0], r[1]) for r in C.missing_rows(rows)]
    assert missing == [("Optim | Minutes", "Normalize")]              # réglages fins : avancés


def test_missing_params_are_listed_with_current_value():
    rows = _without(C.build_rows(), ("Optim | Season", "Opponent model"), ("Optim | Moves", "Min gain"))
    missing = C.missing_rows(rows)
    assert [(r[0], r[1]) for r in missing] == [("Optim | Season", "Opponent model"), ("Optim | Moves", "Min gain")]
    assert missing[1][2] == 0.25 and missing[1][3]


def test_alias_counts_as_present():
    rows = _without(C.build_rows(), ("Optim | Season", "Short-term horizon (days)")) + [["Settings | Days", "ST", "15", ""]]
    assert ("Optim | Season", "Short-term horizon (days)") not in [(r[0], r[1]) for r in C.missing_rows(rows)]


def test_complete_inserts_at_end_of_section_without_touching_other_rows(monkeypatch):
    full = C.build_rows()
    rows = _without(full, ("Optim | Season", "Opponent model"), ("Optim | Moves", "Min gain"))
    rows = [r for r in rows if not r[0].startswith("Optim | History")]       # section entière absente
    before = [list(r) for r in rows]
    ws = FakeWS(rows)
    added = C.complete(FakeBook(ws), "settings")
    assert {(r[0], r[1]) for r in added} >= {("Optim | Season", "Opponent model"), ("Optim | Moves", "Min gain")}
    # lignes d'origine conservées, dans le même ordre
    kept = [r for r in ws.rows if r in before]
    assert kept == before
    sections = [r[0] for r in ws.rows]
    i = sections.index("Optim | Season")
    j = max(k for k, s in enumerate(sections) if s == "Optim | Season")
    assert ws.rows[j][1] == "Opponent model" and all(s == "Optim | Season" for s in sections[i:j + 1])
    assert ws.rows[-1][0].startswith("Optim | History")                     # section absente : en fin d'onglet
    assert C.missing_rows(ws.rows) == []
    assert C.complete(FakeBook(ws), "settings") == []


def test_complete_when_section_is_last_row_of_sheet():
    """Section en fin d'onglet (ajoutée par un Compléter précédent) : insertion après la dernière ligne."""
    rows = C.build_rows()
    minutes = [r for r in rows if C._norm(r[0]) == C._norm("Optim | Minutes")]
    rows = [r for r in rows if C._norm(r[0]) != C._norm("Optim | Minutes")] + minutes[:-1]
    ws = FakeWS(rows)
    added = C.complete(FakeBook(ws), "settings")
    assert [(r[0], r[1]) for r in added] == [(minutes[-1][0], minutes[-1][1])]
    assert ws.rows[-1][1] == minutes[-1][1]


def test_removable_rows_lists_advanced_params_at_default():
    rows = C.build_rows(C.load_default_config()) + [["Optim | Minutes", "Team target", "240", ""],
                                                    ["Optim | Minutes", "Max cut per player", "0,4", ""],
                                                    ["Optim | Season", "Opponent default rate", "0,95", ""],
                                                    ["Optim | History", "Shrink to current weights", "50%", ""]]
    assert C.removable_rows(rows) == [("Optim | Minutes", "Team target"), ("Optim | Season", "Opponent default rate"),
                                      ("Optim | History", "Shrink to current weights")]


class FormulaWS(FakeWS):
    def get(self, rng, value_render_option=None):
        return [list(r) for r in self.rows]


def test_push_refuses_to_erase_formulas(monkeypatch):
    import pytest
    from scripts import sheets

    written = []
    monkeypatch.setattr(sheets, "write_tab", lambda book, tab, rows: written.append(tab))
    ws = FormulaWS([C.HEADER, ["Moves | Compare", '=IF(board!B200="";"";"trade")', "=board!B200", ""]])
    with pytest.raises(ValueError, match="2 formule"):
        C.push(FakeBook(ws), "settings")
    assert written == []
    C.push(FakeBook(FormulaWS([C.HEADER, ["Moves | Compare", "trade", "Pierre", ""]])), "settings")
    assert written[0] == "settings"
