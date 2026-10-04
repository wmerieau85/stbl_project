"""Onglet bdd (projections) : colonnes Team Draft / Team Season à gauche du bloc écrit par le programme.

Disposition (colonne de départ C par défaut, réglable : Sheets | Projections | Start Column) :

    A Team Draft | B Team Season | C Phase | D Player | E Pos | ... (bloc des projections) | formules

- Team Draft : manager qui a drafté le joueur (résultat de la draft, lu sur les lignes de phase
  draft ; recopié depuis Yahoo si « Copy results into the input tab » = oui).
- Team Season : manager qui a le joueur en cours de saison (simulation de saison en mode sheet).

Ce sont des propriétés du joueur : à chaque réécriture du bloc, elles sont reportées sur toutes les
lignes du joueur (toutes phases), y compris s'il change de rang.
"""

import logging

from scripts.names import name_key

log = logging.getLogger(__name__)


def _col_index(letter):
    n = 0
    for ch in letter.strip().upper():
        n = n * 26 + ord(ch) - 64
    return n - 1


def _col_letter(index):
    index += 1
    out = ""
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


def layout(gs):
    """{phase, player, team_draft, team_season} : index (0 = A) des colonnes de l'onglet bdd.
    team_draft / team_season valent None si le bloc commence en A ou B (pas de place à gauche)."""
    start = _col_index(gs.get("projections_start_col") or "C")
    has_teams = start >= 2
    return {"phase": start, "player": start + 1,
            "team_draft": start - 2 if has_teams else None, "team_season": start - 1 if has_teams else None}


def read_rows(book, gs):
    """Lignes de l'onglet bdd, de la colonne A à la colonne Player (en-tête compris)."""
    from scripts.sheets import read_range

    cols = layout(gs)
    tab = gs.get("projections_tab") or "bdd"
    try:
        return read_range(book, tab, f"A1:{_col_letter(cols['player'])}")
    except Exception as exc:  # onglet absent
        log.warning("Onglet %s illisible (%s).", tab, exc)
        return []


def _cell(row, index):
    if index is None or index >= len(row):
        return ""
    return str(row[index] or "").strip()


def team_map(rows, gs):
    """{clé du joueur: (team_draft, team_season)} d'après les lignes actuelles (toutes phases)."""
    cols = layout(gs)
    out = {}
    for r in rows[1:]:
        player = _cell(r, cols["player"])
        if not player:
            continue
        key = name_key(player)
        draft, season = out.get(key, ("", ""))
        is_draft_row = _cell(r, cols["phase"]).lower() == "draft"
        d, s = _cell(r, cols["team_draft"]), _cell(r, cols["team_season"])
        # la ligne de phase draft fait foi pour Team Draft ; sinon la première valeur trouvée
        if d and (is_draft_row or not draft):
            draft = d
        if s and not season:
            season = s
        out[key] = (draft, season)
    return out


def team_columns(block_rows, mapping):
    """Colonnes Team Draft / Team Season alignées sur le bloc à écrire ([Phase, Player, ...])."""
    out = [["Team Draft", "Team Season"]]
    for r in block_rows[1:]:
        out.append(list(mapping.get(name_key(str(r[1])), ("", ""))))
    return out


def draft_assignments(rows, gs):
    """[(manager, joueur)] : lignes de phase draft dont la colonne Team Draft est remplie, dans l'ordre."""
    cols = layout(gs)
    if cols["team_draft"] is None:
        return []
    out = []
    for r in rows[1:]:
        manager, player = _cell(r, cols["team_draft"]), _cell(r, cols["player"])
        if manager and player and _cell(r, cols["phase"]).lower() == "draft":
            out.append((manager, player))
    return out


def season_assignments(rows, gs):
    """[(manager, joueur)] pour la saison : Team Season, sinon (colonne vide partout) Team Draft."""
    mapping = team_map(rows, gs)
    names = {}
    cols = layout(gs)
    for r in rows[1:]:
        player = _cell(r, cols["player"])
        if player:
            names.setdefault(name_key(player), player)
    season = [(s, names[k]) for k, (d, s) in mapping.items() if s]
    if season:
        return season, "Team Season"
    return [(d, names[k]) for k, (d, s) in mapping.items() if d], "Team Draft"


def write_team_draft(book, gs, picks_by_player):
    """Recopie {joueur: manager} dans la colonne Team Draft (toutes les lignes du joueur).
    Les autres valeurs de la colonne (saisies manuelles) sont conservées. Renvoie le nombre de cellules modifiées."""
    cols = layout(gs)
    if cols["team_draft"] is None:
        return 0
    rows = read_rows(book, gs)
    wanted = {name_key(p): m for p, m in picks_by_player.items()}
    column, changed = [], 0
    for r in rows[1:]:
        current = _cell(r, cols["team_draft"])
        value = wanted.get(name_key(_cell(r, cols["player"])), current)
        changed += value != current
        column.append([value])
    if changed:
        letter = _col_letter(cols["team_draft"])
        book.worksheet(gs.get("projections_tab") or "bdd").update(
            values=column, range_name=f"{letter}2:{letter}{len(column) + 1}", value_input_option="RAW")
    return changed
