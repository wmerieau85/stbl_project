"""Nettoyage et normalisation des noms de joueurs (version unique pour tout le projet)."""

import re
import unicodedata

_INJURY_TAGS = re.compile(r"\b(DTD|GTD|OUT|INJ|SUSP)\b")
_SUFFIXES = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


def strip_accents(text):
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if not unicodedata.combining(c))


def clean_display_name(raw_name):
    """Nom lisible : retire statuts de blessure, parenthèses et espaces superflus.

    Les accents et les suffixes (Jr., III...) sont conservés pour l'affichage.
    """
    if raw_name is None:
        return ""
    text = str(raw_name)
    text = re.sub(r"\([^)]*\)", " ", text)
    text = _INJURY_TAGS.sub(" ", text)
    return " ".join(text.split()).strip()


LOST_CHARS = "?\ufffd"   # caractères perdus par un mauvais encodage (Jokić -> Joki?)


def has_lost_chars(text):
    return any(c in (text or "") for c in LOST_CHARS)


def match_lost_chars(key, candidates):
    """Clé contenant des caractères perdus (« nikola joki? ») -> clé candidate unique qui
    correspond (« nikola jokic »), sinon None. Chaque caractère perdu vaut 1 ou 2 lettres."""
    if not has_lost_chars(key):
        return None
    pattern = "".join(".{1,2}" if c in LOST_CHARS else re.escape(c) for c in key)
    regex = re.compile(f"^{pattern}$")
    found = [c for c in candidates if not has_lost_chars(c) and regex.match(c)]
    return found[0] if len(found) == 1 else None


def name_key(name):
    """Clé de rapprochement entre sources.

    "Nikola Jokić" -> "nikola jokic", "Jaren Jackson Jr." -> "jaren jackson",
    "De'Aaron Fox" -> "deaaron fox", "Shai Gilgeous-Alexander" -> "shai gilgeous alexander".
    """
    text = strip_accents(clean_display_name(name)).lower()
    text = text.replace("’", "").replace("'", "").replace(".", "")
    text = re.sub(r"[-_,]", " ", text)
    text = _SUFFIXES.sub(" ", text)
    return " ".join(text.split())
