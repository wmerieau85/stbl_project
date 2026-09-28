"""Onglet « config » du classeur de draft <-> config/league.json.

Deux zones dans l'onglet :
1. paramètres « Section | Paramètre | Valeur | Aide » (une ligne par réglage) ;
2. sous la ligne d'en-tête « Ordre | Manager | Keeper 1 | Keeper 2 », un manager par ligne,
   dans l'ordre du 1er tour. Autant de colonnes Keeper que nécessaire.

    python -m scripts.draft config-push   # crée / réécrit l'onglet à partir de league.json
    python -m scripts.draft config-pull   # met à jour league.json à partir de l'onglet

L'identifiant du classeur de draft et le chemin du compte de service restent dans les
fichiers JSON (il faut les connaître pour pouvoir lire l'onglet).
"""

import json
import logging

from scripts.config import LEAGUE_PATH, _read_json

log = logging.getLogger(__name__)

MANAGERS_HEADER = "Ordre"

# (section, libellé, chemin dans league.json, type, aide)
PARAMS = [
    ("Ligue", "Format", "format", "choice:roto,h2h", "roto ou h2h"),
    ("Ligue", "Plateforme", "platform", "str", "yahoo (positions Yahoo)"),
    ("Ligue", "Nombre d'équipes", "teams", "int", "doit correspondre au nombre de managers ci-dessous"),
    ("Roster", "G", "roster.G", "int", "postes titulaires Guard"),
    ("Roster", "F", "roster.F", "int", "postes titulaires Forward"),
    ("Roster", "C", "roster.C", "int", "postes titulaires Center"),
    ("Roster", "UTIL", "roster.UTIL", "int", "postes titulaires Util"),
    ("Roster", "BN", "roster.BN", "int", "banc"),
    ("Roster", "IL", "roster.IL", "int", "blessés"),
    ("Catégories", "FG%", "categories.fgp", "float", "poids (0 = catégorie ignorée)"),
    ("Catégories", "3PM", "categories.fg3m", "float", ""),
    ("Catégories", "FT%", "categories.ftp", "float", ""),
    ("Catégories", "REB", "categories.reb", "float", ""),
    ("Catégories", "AST", "categories.ast", "float", ""),
    ("Catégories", "STL", "categories.stl", "float", ""),
    ("Catégories", "BLK", "categories.blk", "float", ""),
    ("Catégories", "TO", "categories.tov", "float", ""),
    ("Catégories", "PTS", "categories.pts", "float", ""),
    ("Matchs", "Matchs par poste titulaire", "games.per_slot", "int", "82 x postes titulaires = plafond"),
    ("Matchs", "Alignements", "games.lineup", "choice:daily,weekly", "daily ou weekly"),
    ("Z-scores", "Matchs minimum", "zscore.min_gp", "int", "matchs projetés pour entrer dans le groupe de référence"),
    ("Z-scores", "Itérations", "zscore.iterations", "int", ""),
    ("Draft", "Type", "draft.type", "choice:snake", "snake"),
    ("Draft", "Nombre de tours", "draft.rounds", "int", "tours de keepers compris"),
    ("Draft", "Tours des keepers", "draft.keeper_rounds", "intlist", "ex. 1, 2 (vide = keepers en plus des tours)"),
    ("Draft", "Mon équipe", "draft.my_team", "str", "nom du manager, identique à la liste ci-dessous"),
    ("Draft", "Candidats évalués", "draft.candidates", "int", "joueurs testés à chaque recalcul"),
    ("Draft", "Simulations", "draft.simulations", "int", "tirages de la suite de la draft par candidat"),
    ("Draft", "Bruit ADP", "draft.adp_noise", "float", "0,15 = les managers s'écartent de ~15 % de l'ADP"),
    ("Sheets", "Onglet des choix", "google_sheets.picks_tab", "str", ""),
    ("Sheets", "Plage des choix", "google_sheets.picks_range", "str", "tour, choix, clé, joueur"),
    ("Sheets", "Onglet des recommandations", "google_sheets.reco_tab", "str", ""),
    ("Sheets", "Onglet des projections", "google_sheets.projections_tab", "str", "écrit par push-projections"),
    ("Sheets", "Colonne de début des projections", "google_sheets.projections_start_col", "str", ""),
    ("Sheets", "Veille (secondes)", "google_sheets.poll_seconds", "int", "fréquence de lecture des choix"),
]


def _get(data, path):
    for part in path.split("."):
        if not isinstance(data, dict) or part not in data:
            return None
        data = data[part]
    return data


def _set(data, path, value):
    parts = path.split(".")
    for part in parts[:-1]:
        data = data.setdefault(part, {})
    data[parts[-1]] = value


def _display(value, kind):
    if value is None:
        return ""
    if kind == "intlist":
        return ", ".join(str(v) for v in value)
    return value


def _parse(raw, kind, label):
    text = str(raw).strip() if raw is not None else ""
    if kind == "intlist":
        return [int(float(v)) for v in text.replace(";", ",").split(",") if v.strip()]
    if text == "":
        raise ValueError(f"Onglet config : valeur vide pour « {label} ».")
    if kind == "int":
        return int(float(text.replace(",", ".")))
    if kind == "float":
        return float(text.replace(",", "."))
    if kind.startswith("choice:"):
        allowed = kind.split(":", 1)[1].split(",")
        if text.lower() not in allowed:
            raise ValueError(f"Onglet config : « {label} » = '{text}', attendu : {', '.join(allowed)}.")
        return text.lower()
    return text


def build_rows(league):
    rows = [["Section", "Paramètre", "Valeur", "Aide"]]
    for section, label, path, kind, help_text in PARAMS:
        rows.append([section, label, _display(_get(league, path), kind), help_text])
    rows.append([])
    draft = league.get("draft", {})
    keepers = draft.get("keepers", {}) or {}
    n_keep = max([2] + [len(v or []) for v in keepers.values()])
    rows.append([MANAGERS_HEADER, "Manager"] + [f"Keeper {i}" for i in range(1, n_keep + 1)])
    for i, team in enumerate(draft.get("order", []), 1):
        names = list(keepers.get(team, []) or [])
        rows.append([i, team] + names + [""] * (n_keep - len(names)))
    return rows


def parse_rows(rows, league):
    """Applique le contenu de l'onglet à une copie de league (dict) et la renvoie."""
    by_label = {(s.lower(), l.lower()): (path, kind, l) for s, l, path, kind, _ in PARAMS}
    updated = json.loads(json.dumps(league))
    managers_start = None
    for i, r in enumerate(rows):
        r = [str(c).strip() if c is not None else "" for c in r] + ["", "", ""]
        if r[0].lower() == MANAGERS_HEADER.lower():
            managers_start = i + 1
            break
        key = (r[0].lower(), r[1].lower())
        if key in by_label:
            path, kind, label = by_label[key]
            _set(updated, path, _parse(r[2], kind, label))
    if managers_start is None:
        raise ValueError(f"Onglet config : ligne d'en-tête « {MANAGERS_HEADER} | Manager | Keeper 1... » introuvable.")

    order, keepers = [], {}
    for r in rows[managers_start:]:
        r = [str(c).strip() if c is not None else "" for c in r] + ["", ""]
        if not r[1]:
            continue
        team = r[1]
        order.append((float(r[0].replace(",", ".")) if r[0] else len(order) + 1, team))
        keepers[team] = [name for name in r[2:] if name]
    order = [team for _, team in sorted(order, key=lambda x: x[0])]
    if len(order) != int(updated["teams"]):
        raise ValueError(f"Onglet config : {len(order)} managers pour {updated['teams']} équipes.")
    updated["draft"]["order"] = order
    updated["draft"]["keepers"] = keepers
    if updated["draft"].get("my_team") not in order:
        raise ValueError(f"Onglet config : « Mon équipe » ({updated['draft'].get('my_team')}) absente des managers.")
    return updated


def save_league(league):
    with open(LEAGUE_PATH, "w", encoding="utf-8") as fh:
        json.dump(league, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def pull(spreadsheet, tab):
    """Onglet -> league.json. Renvoie la configuration mise à jour."""
    rows = spreadsheet.worksheet(tab).get("A1:Z200")
    current = _read_json(LEAGUE_PATH)
    updated = parse_rows(rows, current)
    if updated != current:
        save_league(updated)
        log.info("[Config] league.json mis à jour depuis l'onglet '%s'.", tab)
    else:
        log.info("[Config] Onglet '%s' identique à league.json.", tab)
    return updated


def push(spreadsheet, tab, league):
    from scripts.sheets import write_tab

    rows = build_rows(league)
    write_tab(spreadsheet, tab, rows)
    try:
        ws = spreadsheet.worksheet(tab)
        managers_row = next(i for i, r in enumerate(rows, 1) if r and r[0] == MANAGERS_HEADER)
        ws.format("A1:D1", {"textFormat": {"bold": True}, "backgroundColor": {"red": 0.85, "green": 0.9, "blue": 1}})
        ws.format(f"A{managers_row}:Z{managers_row}",
                  {"textFormat": {"bold": True}, "backgroundColor": {"red": 0.85, "green": 0.9, "blue": 1}})
        ws.format(f"C2:C{managers_row - 2}", {"backgroundColor": {"red": 1, "green": 0.97, "blue": 0.8}})
        ws.freeze(rows=1)
    except Exception as exc:  # la mise en forme est un bonus
        log.debug("Mise en forme de l'onglet config ignorée : %s", exc)
