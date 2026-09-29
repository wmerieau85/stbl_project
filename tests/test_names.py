import json

from scripts.config import ALIASES_PATH
from scripts.names import has_lost_chars, match_lost_chars, name_key, strip_accents


def test_name_key_accents_suffixes_punctuation():
    assert name_key("Nikola Jokić") == "nikola jokic"
    assert name_key("Jaren Jackson Jr.") == "jaren jackson"
    assert name_key("De'Aaron Fox") == "deaaron fox"
    assert name_key("Shai Gilgeous-Alexander") == name_key("Shai Gilgeous Alexander")
    assert name_key("Kristaps Porziņģis") == "kristaps porzingis"


def test_strip_accents():
    assert strip_accents("Luka Dončić") == "Luka Doncic"
    assert strip_accents("Ante Žižić") == "Ante Zizic"


def test_lost_chars_match_unique_candidate():
    keys = ["nikola jokic", "nikola jovic", "luka doncic"]
    assert has_lost_chars("nikola joki?")
    assert match_lost_chars("nikola joki?", keys) == "nikola jokic"
    assert match_lost_chars("luka don?i?", keys) == "luka doncic"
    assert match_lost_chars("nikola jo?i?", keys) is None      # ambigu : Jokic / Jovic
    assert match_lost_chars("nikola jokic", keys) is None      # rien de perdu


def test_aliases_are_clean():
    aliases = json.load(open(ALIASES_PATH, encoding="utf-8"))
    assert not [k for k in aliases if has_lost_chars(k) or has_lost_chars(aliases[k])]
    assert not [k for k, v in aliases.items() if k == v]
    assert not [v for v in aliases.values() if strip_accents(v) != v]
