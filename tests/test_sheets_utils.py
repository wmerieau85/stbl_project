import pytest

from scripts.sheets import cell_number, column_index, column_letter


@pytest.mark.parametrize("letter,index", [("A", 0), ("c", 2), (" Z ", 25), ("AA", 26), ("BK", 62), ("ZZ", 701)])
def test_column_index_and_letter_round_trip(letter, index):
    assert column_index(letter) == index
    assert column_letter(index) == letter.strip().upper()


@pytest.mark.parametrize("bad", ["", "A1", "É", "  "])
def test_column_index_rejects_non_letters(bad):
    with pytest.raises(ValueError):
        column_index(bad)


@pytest.mark.parametrize("text,value", [("12,5", 12.5), ("1 234,5", 1234.5), ("1 234", 1234.0), (" 0.25 ", 0.25), (3, 3.0)])
def test_cell_number_reads_sheet_formats(text, value):
    assert cell_number(text) == value


def test_cell_number_rejects_text():
    with pytest.raises(ValueError):
        cell_number("abc")
