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
