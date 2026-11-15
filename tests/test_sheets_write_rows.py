"""write_rows : seules les lignes visées sont vidées puis écrites (blocs de moves_reco)."""
from scripts import sheets


class FakeWS:
    def __init__(self, rows=100, cols=26):
        self.row_count, self.col_count = rows, cols
        self.cleared, self.updates = [], []

    def resize(self, rows, cols):
        self.row_count, self.col_count = rows, cols

    def batch_clear(self, ranges):
        self.cleared += ranges

    def update(self, values, range_name, value_input_option=None):
        self.updates.append((range_name, values))


class FakeBook:
    def __init__(self, ws):
        self.ws = ws

    def worksheet(self, tab):
        return self.ws


def test_write_rows_keeps_other_block():
    ws = FakeWS(cols=40)
    sheets.write_rows(FakeBook(ws), "moves_reco", [["a"], ["b", 1.23456789]], 1, 39)
    assert ws.cleared == ["A1:AN39"] and ws.updates == [("A1", [["a", ""], ["b", 1.234568]])]
    ws = FakeWS(rows=50, cols=40)
    sheets.write_rows(FakeBook(ws), "moves_reco", [["sim"]], 40)
    assert ws.cleared == ["A40:AN50"] and ws.updates[0][0] == "A40"


def test_write_rows_truncates_to_its_block():
    ws = FakeWS()
    sheets.write_rows(FakeBook(ws), "moves_reco", [[i] for i in range(50)], 1, 39)
    assert len(ws.updates[0][1]) == 39
