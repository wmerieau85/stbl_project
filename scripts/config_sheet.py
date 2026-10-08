"""Onglet « settings » du classeur : il fait foi pour toute la configuration.

Une ligne par information, quatre colonnes : Section | Paramètre | Valeur | Aide.
La section est de la forme « Domaine | Sous-domaine » (ex. « Settings | League »). L'ordre des
lignes est libre, les lignes vides sont ignorées. Sections reconnues :

- paramètres simples (PARAMS ci-dessous) : Settings, Sheets, Optim, Pipeline ;
- listes : Draft | Order (rang -> manager), Draft | Keepers (manager -> joueur),
  Transco | Managers (manager -> équipe Yahoo) ;
- sources (paramètre = code du site : cbs, fp, fs, dk, le, nc, fn) : Sources | Types, Sources | Draft,
  Sources | RoS, Sources | Active (paramètre = étape draft / ros, valeur = code), Sources | Players,
  Sources | Import Files, Sources | <fenêtre> (Sea, L30, L15, L07 : stats réelles par période) ;
- lecture des fichiers et pages des sites : Transco | <colonne> (gp, min, pts... : paramètre =
  code du site, valeur = nom de la colonne chez le site, plusieurs lignes possibles),
  Transco | percent_scale, Stat Mode, Empty as Zero, Missing <colonne>, Skip Players, Player,
  Team, Positions, Page Positions ;
- grilles de pondération : Grid <phase> | GP / MIN / STATS / <catégorie> (paramètre = code de
  la ligne « site », valeur = poids).

Les alias de joueurs sont dans un onglet à part (Sheets | Tab | Players source, players).
Tirage au sort de l'ordre de draft : sections Draft Lottery (plages de l'onglet board).

Une liste ou une grille présente dans l'onglet remplace entièrement celle de la configuration
locale ; un paramètre absent garde sa valeur. La configuration locale (config/config.json,
ignorée par git) n'est qu'une copie, réécrite à chaque relecture de l'onglet :

    python -m scripts.config_sheet pull      # onglets settings + players -> config/config.json
    python -m scripts.config_sheet push      # config/config.json -> onglets (remise à plat)
    python -m scripts.config_sheet migrate   # ancien format de l'onglet -> nouveau (une fois)
"""

import argparse
import json
import logging
import re
import sys

from scripts.config import (CATEGORY_LABELS_SHEET, load_bootstrap, load_config, load_default_config, save_bootstrap,
                            save_config, validate_runtime_config)

log = logging.getLogger(__name__)

HEADER = ["Section", "Paramètre", "Valeur", "Aide"]
ALIAS_HEADER = ["Nom lu dans une source", "Nom de référence"]
MAIN_STAGES = {"draft": "Draft", "ros": "RoS"}

# (section, paramètre, cible, chemin, type, aide). Cibles : league, settings, bootstrap.
PARAMS = [
    ("Settings | League", "Actual Season", "settings", "active_season", "str", "ex. 2026-27"),
    ("Settings | League", "Actual Phase", "settings", "active_stage", "choice:draft,ros",
     "draft (avant la saison) ou ros (en cours de saison : phases lt et st)"),
    ("Settings | League", "Scoring", "league", "format", "choice:roto,h2h", "roto ou h2h"),
    ("Settings | League", "Platform", "league", "platform", "str", "yahoo (positions Yahoo)"),
    ("Settings | League", "Number of teams", "league", "teams", "int", "doit correspondre au nombre de managers (Draft | Order)"),
    ("Settings | League", "League ID", "league", "yahoo.league_id", "optstr",
     "basketball.fantasysports.yahoo.com/nba/<ID> (change chaque saison)"),
    ("Settings | League", "Yahoo rankings count", "league", "yahoo.rankings_count", "int",
     "joueurs du pré-classement Yahoo récupérés (colonnes Yahoo / ADP Yahoo de bdd)"),
    ("Settings | League", "My team", "league", "draft.my_team", "str", "nom du manager, identique à Draft | Order"),
    ("Settings | League", "Maximum games", "league", "games.per_slot", "int", "82 x postes titulaires = plafond"),
    ("Settings | League", "Roster changes", "league", "games.lineup", "choice:daily,weekly", "daily ou weekly"),
    ("Settings | Phases", "Long Term", "league", "season.lt_phase", "str", "grille utilisée pour le reste de la saison"),
    ("Settings | Phases", "Short Term", "league", "season.st_phase", "str", "grille utilisée pour l'horizon court terme"),
    ("Settings | Draft", "Type", "league", "draft.type", "choice:snake", "snake"),
    ("Settings | Draft", "Rounds", "league", "draft.rounds", "int", "tours de keepers compris"),
    ("Settings | Draft", "Keeper rounds", "league", "draft.keeper_rounds", "intlist",
     "ex. 1, 2 (vide = keepers en plus des tours)"),
    ("Sheets | Settings", "ID", "bootstrap", "spreadsheet_id", "str",
     "classeur lu (indicatif : l'ID utilisé est celui de config/bootstrap.json)"),
    ("Sheets | Settings", "Projections ID", "league", "google_sheets.projections_spreadsheet_id", "optstr",
     "autre classeur pour l'onglet des projections (vide = ce classeur)"),
    ("Sheets | Tab", "Config", "bootstrap", "config_tab", "str", "cet onglet (indicatif : nom lu dans config/bootstrap.json)"),
    ("Sheets | Tab", "Season", "league", "google_sheets.season_tab", "str", "classement réel et projeté, mon effectif"),
    ("Sheets | Tab", "Players source", "league", "google_sheets.alias_tab", "str",
     "alias : noms lus dans les sources -> nom de référence"),
    ("Sheets | Tab", "Draft reco", "league", "google_sheets.reco_tab", "str", "réécrit à chaque recalcul"),
    ("Sheets | Tab", "Projections", "league", "google_sheets.projections_tab", "str",
     "toutes les phases + Team Draft / Team Season, écrit par push-projections / --push-sheet"),
    ("Sheets | Tab", "Board", "league", "google_sheets.board_tab", "str", "draft board et tirage au sort (formules)"),
    ("Sheets | Tab", "Detail", "league", "google_sheets.detail_tab", "str",
     "projections de chaque source à côté de la pondérée (vide = pas d'onglet)"),
    ("Sheets | Draft", "Choices Source", "league", "draft.picks_source", "choice:yahoo,sheet",
     "yahoo (lecture en direct) ou sheet (colonne Team Draft de l'onglet Projections, lignes draft)"),
    ("Sheets | Draft", "Copy results into the input tab", "league", "google_sheets.write_picks_to_sheet", "bool",
     "oui / non : choix Yahoo recopiés dans la colonne Team Draft (les autres saisies sont gardées)"),
    ("Sheets | Projections", "Start Column", "league", "google_sheets.projections_start_col", "str",
     "colonne Phase ; les 2 colonnes à gauche = Team Draft / Team Season, les formules à droite sont gardées"),
    ("Sheets | Season", "Rosters Source", "league", "season.roster_source", "choice:yahoo,sheet",
     "yahoo (effectifs réels) ou sheet (colonne Team Season de l'onglet Projections, sinon Team Draft)"),
    ("Optim | Draft", "Minimum number of games", "league", "zscore.min_gp", "int",
     "z-scores : matchs projetés pour entrer dans le groupe de référence"),
    ("Optim | Draft", "Iterations", "league", "zscore.iterations", "int", "z-scores : recalculs du groupe de référence"),
    ("Optim | Draft", "Standby interval (seconds)", "league", "google_sheets.poll_seconds", "int",
     "watch : lecture des choix toutes les N secondes"),
    ("Optim | Draft", "Evaluated Candidates", "league", "draft.candidates", "int", "joueurs testés à chaque recalcul"),
    ("Optim | Draft", "Simulations", "league", "draft.simulations", "int",
     "tirages maximum de la suite de la draft (candidats finalistes)"),
    ("Optim | Draft", "Simulations min", "league", "draft.simulations_min", "int",
     "tirages pour tous les candidats avant la première élimination"),
    ("Optim | Draft", "Finalists", "league", "draft.finalists", "int",
     "candidats toujours simulés jusqu'au bout"),
    ("Optim | Draft", "Prune z", "league", "draft.prune_z", "float",
     "écart (en écarts-types) au meilleur au-delà duquel un candidat est écarté"),
    ("Optim | Draft", "Time budget", "league", "draft.time_budget", "float",
     "secondes maximum par recommandation (0 = pas de limite)"),
    ("Optim | Draft", "ADP Noise", "league", "draft.adp_noise", "float", "0,15 = les managers s'écartent de ~15 % de l'ADP"),
    ("Optim | Draft", "Opponent model", "league", "draft.opponent_model", "str",
     "adp = marché seul ; need = l'ADP départagé par les besoins de chaque équipe"),
    ("Optim | Draft", "Need weight", "league", "draft.need_weight", "float",
     "mode need : poids des besoins au dernier tour (0 au 1er tour, croissant ensuite)"),
    ("Optim | Draft", "Need candidates", "league", "draft.need_candidates", "int",
     "mode need : prochains joueurs de l'ADP départagés par les besoins"),
    ("Optim | Season", "Short-term horizon (days)", "league", "season.st_days", "int",
     "jours projetés avec la phase st, ensuite lt"),
    ("Optim | Season", "Simulations", "league", "season.simulations", "int", "tirages pour les probabilités de classement"),
    ("Pipeline | Import", "Maximum time per page (s)", "settings", "http.timeout", "int", ""),
    ("Pipeline | Import", "Attempts", "settings", "http.retries", "int", "nouvelles tentatives en cas d'échec"),
    ("Pipeline | Import", "Pause between pages (s)", "settings", "http.pause_seconds", "float", ""),
    ("Pipeline | Export", "CSV Delimiter", "settings", "export.delimiter", "str", "; pour Excel en français"),
    ("Pipeline | Export", "Decimal separator", "settings", "export.decimal", "str", ","),
    ("Draft Lottery | Managers", "Cells Range", "league", "lottery.managers_range", "str", "managers du tirage"),
    ("Draft Lottery | Active", "Cells Range", "league", "lottery.active_range", "str",
     "0 = manager encore dans le tirage (pas encore placé dans l'ordre final)"),
    ("Draft Lottery | Number Balls", "Cells Range", "league", "lottery.balls_range", "str",
     "boules par manager : 1re colonne = tirage 1, 2e colonne = tirage 2"),
    ("Draft Lottery | Lottery Number", "Cells Range", "league", "lottery.number_range", "str", "tirage en cours : 1 ou 2"),
    ("Draft Lottery | Balls", "Cells Range", "league", "lottery.output_range", "str",
     "où écrire les boules générées (Ball | Manager), onglet créé si absent"),
]

# lignes d'anciennes versions de l'onglet, devenues sans objet : ignorées sans erreur
OBSOLETE = {("sheets | tab", "draft results"), ("sheets | tab", "rosters"), ("sheets | draft", "cells range")}

# autres libellés acceptés (versions de travail de l'onglet) -> (section, paramètre) officiels
PARAM_ALIASES = {
    ("sheets | season", "choices source"): ("Sheets | Season", "Rosters Source"),
    ("settings | days", "st"): ("Optim | Season", "Short-term horizon (days)"),
}

ROSTER_SECTION = "Settings | Roster Positions"
SCORING_SECTION = "Settings | Scoring"
ORDER_SECTION = "Draft | Order"
KEEPERS_SECTION = "Draft | Keepers"
MANAGERS_SECTION = "Transco | Managers"
ROSTER_HELP = {"G": "postes titulaires Guard", "F": "postes titulaires Forward", "C": "postes titulaires Center",
               "UTIL": "postes titulaires Util", "BN": "banc", "IL": "blessés"}

# options de lecture des sources : paramètre de l'onglet -> clé de la configuration des sources
SOURCE_OPTIONS = {
    "percent_scale": ("percent_scale", "float", "valeur lue / échelle = pourcentage entre 0 et 1 (100 si 48.7 = 48,7 %)"),
    "stat mode": ("stat_mode", "str", "auto, total ou per_game"),
    "empty as zero": ("empty_as_zero", "bool", "cellule vide = 0"),
    "skip players": ("skip_players", "list", "joueur ignoré (une ligne par nom)"),
    "player": ("player_column", "str", "colonne du nom du joueur"),
    "team": ("team_column", "str", "colonne de l'équipe NBA"),
    "positions": ("positions_column", "str", "colonne des postes"),
    "page positions": ("positions", "csvlist", "une page par poste (CBS)"),
}
OPTION_LABELS = {"percent_scale": "percent_scale", "stat_mode": "Stat Mode", "empty_as_zero": "Empty as Zero",
                 "skip_players": "Skip Players", "player_column": "Player", "team_column": "Team",
                 "positions_column": "Positions", "positions": "Page Positions"}


# --------------------------------------------------------------------------- utilitaires

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
    if kind in ("intlist", "csvlist"):
        return ", ".join(str(v) for v in value)
    if kind == "bool":
        return "oui" if value else "non"
    return value


def _parse_bool(text, label):
    text = str(text or "").strip().lower()
    if text in ("oui", "o", "yes", "y", "true", "vrai", "1"):
        return True
    if text in ("non", "n", "no", "false", "faux", "0"):
        return False
    raise ValueError(f"Onglet config : « {label} » = '{text}', attendu : oui ou non.")


def _number(text, label):
    try:
        return float(str(text).strip().replace("\u00a0", "").replace(" ", "").replace(",", "."))
    except ValueError:
        raise ValueError(f"Onglet config : « {label} » = '{text}', nombre attendu.") from None


def _parse(raw, kind, label):
    text = str(raw).strip() if raw is not None else ""
    if kind == "intlist":
        return [int(_number(v, label)) for v in text.replace(";", ",").split(",") if v.strip()]
    if kind == "csvlist":
        return [v.strip() for v in text.replace(";", ",").split(",") if v.strip()]
    if kind == "optstr":
        return text
    if kind == "bool":
        return _parse_bool(text, label)
    if text == "":
        raise ValueError(f"Onglet config : valeur vide pour « {label} ».")
    if kind == "int":
        return int(_number(text, label))
    if kind == "float":
        value = _number(text, label)
        return int(value) if value.is_integer() and label.endswith("percent_scale") else value
    if kind.startswith("choice:"):
        allowed = kind.split(":", 1)[1].split(",")
        if text.lower() not in allowed:
            raise ValueError(f"Onglet config : « {label} » = '{text}', attendu : {', '.join(allowed)}.")
        return text.lower()
    return text


def _norm(text):
    return " ".join(str(text or "").split()).lower()


def _split_section(section):
    parts = [p.strip() for p in str(section).split("|", 1)]
    return (parts[0], parts[1] if len(parts) > 1 else "")


def _clean_rows(rows):
    out = []
    for r in rows:
        cells = [str(c).strip() if c is not None else "" for c in r] + ["", "", "", ""]
        if cells[0] and cells[0].lower() != "section":
            out.append(cells[:4])
    return out


def _codes(sources):
    return {conf.get("code"): name for name, conf in sources.items() if conf.get("code")}


def _window_label(stage):
    return stage.upper() if re.fullmatch(r"l\d+", stage) else stage.capitalize()


# --------------------------------------------------------------------------- écriture de l'onglet

def build_rows(cfg=None, bootstrap=None):
    cfg = cfg or load_config()
    boot = bootstrap or load_bootstrap()
    from scripts.config import DEFAULT_LEAGUE, DEFAULT_SETTINGS, _deep_merge

    league = _deep_merge(DEFAULT_LEAGUE, cfg["league"])
    for key in ("roster", "categories"):
        if key in cfg["league"]:
            league[key] = cfg["league"][key]
    settings = _deep_merge(DEFAULT_SETTINGS, cfg["settings"])
    sources = cfg["sources"]
    targets = {"league": league, "settings": settings, "bootstrap": boot}
    rows = [HEADER]

    def add(section, param, value, help_text=""):
        rows.append([section, param, value, help_text])

    params = {(s, p): (t, path, kind, h) for s, p, t, path, kind, h in PARAMS}
    order = [("Settings | League", None), (ROSTER_SECTION, "roster"), (SCORING_SECTION, "scoring"),
             ("Settings | Phases", None), ("Settings | Draft", None), ("Sheets | Settings", None),
             ("Sheets | Tab", None), ("Sheets | Draft", None), ("Sheets | Projections", None),
             ("Sheets | Season", None), ("Optim | Draft", None), ("Optim | Season", None),
             ("Pipeline | Import", None), ("Pipeline | Export", None), ("Draft Lottery | Managers", None),
             ("Draft Lottery | Active", None), ("Draft Lottery | Number Balls", None),
             ("Draft Lottery | Lottery Number", None), ("Draft Lottery | Balls", None)]
    for section, special in order:
        if special == "roster":
            for pos, n in league["roster"].items():
                add(section, pos, n, ROSTER_HELP.get(pos, "postes"))
            continue
        if special == "scoring":
            for i, (cat, w) in enumerate(league["categories"].items()):
                add(section, CATEGORY_LABELS_SHEET.get(cat, cat), w, "poids (0 = catégorie ignorée)" if i == 0 else "")
            continue
        for (s, p), (t, path, kind, h) in params.items():
            if s == section:
                add(s, p, _display(_get(targets[t], path), kind), h)

    draft = league["draft"]
    for i, manager in enumerate(draft.get("order", []), 1):
        add(ORDER_SECTION, i, manager, "ordre du 1er tour" if i == 1 else "")
    for manager, players in (draft.get("keepers") or {}).items():
        for player in players or []:
            add(KEEPERS_SECTION, manager, player, "sans effet si les choix viennent de Yahoo (Yahoo fait foi)")
    for manager, team in ((league.get("yahoo") or {}).get("teams") or {}).items():
        add(MANAGERS_SECTION, manager, team, "")

    codes = [(conf.get("code"), name, conf) for name, conf in sources.items() if conf.get("code")]
    for code, _, conf in codes:
        add("Sources | Types", code, "URL" if "urls" in conf else "csv", "URL (page web) ou csv (fichier déposé)")
    for stage, label in MAIN_STAGES.items():
        for code, _, conf in codes:
            target = (conf.get("urls") or conf.get("files") or {}).get(stage)
            if target:
                add(f"Sources | {label}", code, target, "")
    for stage in MAIN_STAGES:
        for code, name, _ in codes:
            if (settings["sources"].get(name) or {}).get(stage, False) if isinstance(settings["sources"].get(name), dict) \
                    else settings["sources"].get(name, False):
                add("Sources | Active", stage, code, "une ligne par source utilisée à cette étape")
    for code, _, conf in codes:
        if conf.get("import_dir"):
            add("Sources | Import Files", code, conf["import_dir"], "dossier où déposer les fichiers")
    for code, _, conf in codes:
        if conf.get("players_url"):
            add("Sources | Players", code, conf["players_url"], "liste des joueurs (équipe, postes)")
    for code, _, conf in codes:
        for stage, url in (conf.get("urls") or {}).items():
            if stage not in MAIN_STAGES:
                add(f"Sources | {_window_label(stage)}", code, url, "stats réelles par période (étape ros)")

    for phase, grid in cfg["grids"].items():
        rows += grid_rows(phase, grid)

    for code, _, conf in codes:
        for key in ("percent_scale", "stat_mode", "empty_as_zero", "skip_players", "player_column", "team_column",
                    "positions_column", "positions"):
            if key not in conf:
                continue
            label = OPTION_LABELS[key]
            kind = SOURCE_OPTIONS[label.lower()][1]
            values = conf[key] if kind == "list" else [_display(conf[key], kind)]
            for v in values:
                add(f"Transco | {label}", code, v, "")
        for col, values in (conf.get("missing_values") or {}).items():
            add(f"Transco | Missing {col}", code, ", ".join(str(v) for v in values), "valeur = absente")
        for site_col, std in (conf.get("columns") or {}).items():
            add(f"Transco | {std}", code, site_col, "")
    return rows


def grid_rows(phase, grid):
    """Grille (disposition CSV : ligne des cases, ligne site, puis GP / MIN / STATS...) -> lignes longues."""
    rows = [[str(c).strip() for c in r] for r in grid if any(str(c).strip() for c in r)]
    site = next((r for r in rows if r and r[0].lower() in ("site", "source", "code")), None)
    if site is None:
        return []
    out = []
    label = "Draft" if phase == "draft" else phase
    for r in rows:
        if r is site or r is rows[0] or not r[0]:
            continue
        for i, code in enumerate(site[1:], 1):
            if code:
                out.append([f"Grid {label} | {r[0]}", code, r[i] if i < len(r) else "", ""])
    return out


# --------------------------------------------------------------------------- lecture de l'onglet

def parse_rows(rows, cfg=None, bootstrap=None):
    """Applique l'onglet à une copie de la configuration. Renvoie (config, bootstrap)."""
    cfg = json.loads(json.dumps(cfg if cfg is not None else load_config()))
    boot = json.loads(json.dumps(bootstrap if bootstrap is not None else load_bootstrap()))
    league, settings, sources = cfg["league"], cfg["settings"], cfg["sources"]
    targets = {"league": league, "settings": settings, "bootstrap": boot}
    params = {(_norm(s), _norm(p)): (t, path, kind, p) for s, p, t, path, kind, _ in PARAMS}
    for alias, official in PARAM_ALIASES.items():
        params[alias] = params[(_norm(official[0]), _norm(official[1]))]
    codes = _codes(sources)
    cat_by_label = {_norm(v): k for k, v in CATEGORY_LABELS_SHEET.items()}

    grouped, unknown, obsolete = {}, set(), set()
    for section, param, value, _ in _clean_rows(rows):
        grouped.setdefault(_norm(section), []).append((section, param, value))

    _sheet_codes = set()   # sources présentes dans l'onglet ; les autres (nouvelles) gardent les valeurs livrées

    def code_of(code, section):
        if code not in codes:
            raise ValueError(f"Onglet config, {section} : code source « {code} » inconnu "
                             f"(codes : {', '.join(sorted(codes))}).")
        _sheet_codes.add(codes[code])
        return codes[code]

    roster, scoring, order, keepers, teams = {}, {}, [], {}, {}
    stages, active, windows, dirs, grids, transco = {}, {}, {}, {}, {}, {}
    players_urls = {}
    for key, items in grouped.items():
        domain, sub = _split_section(key)
        for section, param, value in items:
            pkey = (key, _norm(param))
            if pkey in OBSOLETE:
                obsolete.add(f"{section} | {param}")
            elif pkey in params:
                target, path, kind, label = params[pkey]
                parsed = _parse(value, kind, label)
                if target == "bootstrap":   # le classeur ne peut pas changer l'ID / l'onglet par lesquels on le lit
                    if parsed and parsed != _get(boot, path):
                        log.warning("[Config] %s | %s = « %s » mais config/bootstrap.json indique « %s » : "
                                    "valeur du classeur ignorée (mettez la ligne à jour).", section, param, parsed,
                                    _get(boot, path))
                else:
                    _set(targets[target], path, parsed)
            elif key == _norm(ROSTER_SECTION):
                roster[param.upper()] = int(_number(value, f"{section} {param}"))
            elif key == _norm(SCORING_SECTION):
                if _norm(param) not in cat_by_label:
                    raise ValueError(f"Onglet config, {section} : catégorie « {param} » inconnue.")
                scoring[cat_by_label[_norm(param)]] = _number(value, f"{section} {param}")
            elif key == _norm(ORDER_SECTION):
                if value:
                    order.append((_number(param, f"{section} {param}"), value))
            elif key == _norm(KEEPERS_SECTION):
                if value:
                    keepers.setdefault(param, []).append(value)
            elif key == _norm(MANAGERS_SECTION):
                if value:
                    teams[param] = value
            elif domain == "sources" and sub in ("draft", "ros"):
                if value:
                    stages.setdefault(code_of(param, section), {})[sub] = value
            elif domain == "sources" and sub == "active":
                stage = _norm(param)
                if stage not in MAIN_STAGES:
                    raise ValueError(f"Onglet config, {section} : étape « {param} », attendu draft ou ros.")
                active.setdefault(code_of(value, section), set()).add(stage)
            elif domain == "sources" and sub == "import files":
                dirs[code_of(param, section)] = value
            elif domain == "sources" and sub == "players":
                if value:
                    players_urls[code_of(param, section)] = value
            elif domain == "sources" and sub == "types":
                name = code_of(param, section)
                expected = "url" if "urls" in sources[name] else "csv"
                if _norm(value) != expected:
                    log.warning("[Config] %s : type « %s » ignoré (%s est lu en %s).", section, value, param,
                                expected.upper() if expected == "url" else expected)
            elif domain == "sources" and sub:
                if value:
                    windows.setdefault(code_of(param, section), {})[sub] = value
            elif domain.startswith("grid "):
                phase = domain[5:].strip()
                level = section.split("|", 1)[1].strip() if "|" in section else ""
                if not level:
                    raise ValueError(f"Onglet config, {section} : niveau absent (ex. Grid lt | GP).")
                grids.setdefault(phase, []).append((level, param, value))
            elif domain == "transco" and sub:
                transco.setdefault(code_of(param, section), []).append((sub, value, section))
            else:
                unknown.add(section if pkey[0] else param)
                continue
    if unknown:
        log.warning("[Config] Lignes non reconnues (ignorées) : %s", ", ".join(sorted(unknown)))
    if obsolete:
        log.info("[Config] Lignes devenues inutiles (à supprimer de l'onglet) : %s", ", ".join(sorted(obsolete)))

    if roster:
        league["roster"] = roster
    if scoring:
        league["categories"] = scoring
    draft = league.setdefault("draft", {})
    if order:
        draft["order"] = [m for _, m in sorted(order, key=lambda x: x[0])]
    if KEEPERS_SECTION.lower() in grouped:
        draft["keepers"] = keepers
    if teams:
        league.setdefault("yahoo", {})["teams"] = teams
    _check_league(league)

    if stages:
        for name, conf in sources.items():
            if name not in stages:      # source absente de l'onglet (nouvelle source) : valeurs livrées
                continue
            key = "urls" if "urls" in conf else "files"
            extra = {s: u for s, u in (conf.get(key) or {}).items() if s not in MAIN_STAGES}
            conf[key] = dict(stages.get(name, {}), **extra)
    if windows:   # fenêtres de stats : la liste de l'onglet remplace l'ancienne
        listed = set(_sheet_codes)
        for name, conf in sources.items():
            if "urls" in conf and name in listed:
                conf["urls"] = {s: u for s, u in conf["urls"].items() if s in MAIN_STAGES}
                conf["urls"].update(windows.get(name, {}))
        settings["stats_windows"] = dict({name: w for name, w in (settings.get("stats_windows") or {}).items()
                                          if name not in listed}, **{name: list(w) for name, w in windows.items()})
    if "sources | active" in grouped:
        previous = settings.get("sources") or {}
        default_active = load_default_config().get("settings", {}).get("sources", {})
        settings["sources"] = {name: ({s: s in active.get(name, set()) for s in MAIN_STAGES}
                                      if name in _sheet_codes else previous.get(
                                          name, default_active.get(name, {s: False for s in MAIN_STAGES}))
                                      )
                               for name in sources}
    for name, folder in dirs.items():
        sources[name]["import_dir"] = folder
    for name, url in players_urls.items():
        sources[name]["players_url"] = url
    for name, entries in transco.items():
        _apply_transco(sources[name], entries)

    delivered_grids = load_default_config().get("grids", {})
    new_sources = set(sources) - _sheet_codes
    for phase, cells in grids.items():
        migrate_sources = phase in delivered_grids and new_sources
        grid = _grid_from_cells(phase, cells, settings.get("active_season"), validate=not migrate_sources)
        if phase in delivered_grids and new_sources:
            grid = _add_default_source_weights(phase, grid, delivered_grids[phase], sources, new_sources)
            _validate_grid(phase, grid, settings.get("active_season"))
        cfg["grids"][phase] = grid

    season = league.get("season", {})
    ros = [season.get("lt_phase", "lt"), season.get("st_phase", "st")]
    ros += [p for p in cfg["grids"] if p != "draft" and p not in ros]   # ex. season : stats réelles
    settings.setdefault("phases", {})["ros"] = ros
    return cfg, boot


def _check_league(league):
    order = league.get("draft", {}).get("order") or []
    if order and len(order) != int(league.get("teams", len(order))):
        raise ValueError(f"Onglet config : {len(order)} managers dans Draft | Order pour {league['teams']} équipes.")
    my_team = league.get("draft", {}).get("my_team")
    if order and my_team not in order:
        raise ValueError(f"Onglet config : « My team » ({my_team}) absent de Draft | Order.")
    unknown = [m for m in (league.get("draft", {}).get("keepers") or {}) if order and m not in order]
    if unknown:
        raise ValueError(f"Onglet config, Draft | Keepers : managers inconnus : {', '.join(unknown)}.")


def _apply_transco(conf, entries):
    from scripts.db import NUMERIC_COLUMNS

    columns, missing, options = {}, {}, {}
    for sub, value, section in entries:
        if sub in SOURCE_OPTIONS:
            key, kind, _ = SOURCE_OPTIONS[sub]
            if kind == "list":
                options.setdefault(key, []).append(value)
            else:
                options[key] = _parse(value, kind, section)
        elif sub.startswith("missing "):
            col = section.split("|", 1)[1].strip()[len("missing "):].strip()
            missing[col] = [_maybe_number(v) for v in str(value).replace(";", ",").split(",") if v.strip()]
        elif sub in NUMERIC_COLUMNS:
            if value:
                columns[value] = sub
        else:
            raise ValueError(f"Onglet config, {section} : colonne « {sub} » inconnue "
                             f"(attendu : {', '.join(NUMERIC_COLUMNS)} ou une option de lecture).")
    if columns:
        conf["columns"] = columns
    if missing:
        conf["missing_values"] = missing
    conf.update(options)


def _maybe_number(text):
    try:
        value = float(str(text).strip().replace(",", "."))
        return int(value) if value.is_integer() else value
    except ValueError:
        return str(text).strip()


def _grid_from_cells(phase, cells, season, validate=True):
    """[(niveau, code, poids)] -> grille (disposition CSV), contrôlée avant d'être retenue."""

    levels, codes = [], []
    weights = {}
    for level, code, value in cells:
        if level not in levels:
            levels.append(level)
        if code not in codes:
            codes.append(code)
        weights[(level, code)] = value
    prefix = "lt" if phase == "draft" else phase
    grid = ([[""] + [f"{prefix}{i:02d}" for i in range(1, len(codes) + 1)], ["site"] + codes]
            + [[level] + [weights.get((level, c), "") for c in codes] for level in levels])
    if validate:
        _validate_grid(phase, grid, season)
    return grid


def _validate_grid(phase, grid, season):
    from scripts.weighting.weights import WeightsError, load_grid

    import tempfile
    import csv
    import os

    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8", newline="") as tmp:
        csv.writer(tmp, delimiter=";").writerows(grid)
    try:
        load_grid(phase, season, path=tmp.name)
    except (WeightsError, ValueError) as exc:
        raise ValueError(f"Onglet config, Grid {phase} : {str(exc).replace(tmp.name + ' : ', '')}") from None
    finally:
        os.unlink(tmp.name)


def _add_default_source_weights(phase, grid, delivered_grid, sources, new_sources):
    """Ajoute à une ancienne grille les emplacements livrés pour des sources absentes de l'onglet."""
    from scripts.weighting.weights import parse_weight

    def site_codes(rows):
        row = next((r for r in rows if r and r[0].strip().lower() in ("site", "source", "code")), None)
        return row, [c.strip() for c in row[1:]] if row else []

    current_site, current_codes = site_codes(grid)
    default_site, default_codes = site_codes(delivered_grid)
    if current_site is None or default_site is None:
        return grid

    source_codes = {sources[name].get("code") for name in new_sources if name in sources}
    additions = [
        code for code in default_codes
        if any(code == source_code or re.match(rf"^{re.escape(source_code)}\d{{2}}(?:\.|$)", code)
               for source_code in source_codes if source_code)
        and code not in current_codes
    ]
    if not additions:
        return grid

    default_rows = {row[0].strip().upper(): row for row in delivered_grid[2:] if row}
    updated = [row[:] for row in grid]
    current_site = updated[1]
    current_site.extend(additions)
    prefix = "lt" if phase == "draft" else phase
    start = len(updated[0])
    updated[0].extend(f"{prefix}{i:02d}" for i in range(start, start + len(additions)))

    for row in updated[2:]:
        label = row[0].strip().upper()
        default_row = default_rows.get(label) or default_rows.get("STATS")
        if default_row is None:
            continue
        new_weights = [
            parse_weight(default_row[default_codes.index(code) + 1])
            if default_codes.index(code) + 1 < len(default_row) else 0.0
            for code in additions
        ]
        remaining = 1.0 - sum(new_weights)
        old_weights = [parse_weight(value) for value in row[1:len(current_codes) + 1]]
        old_total = sum(old_weights)
        if remaining < 0 or (old_total <= 0 and remaining > 0):
            raise ValueError(f"Grid : impossible d'ajouter les nouvelles sources à la ligne {row[0]}.")
        scaled = [weight * remaining / old_total for weight in old_weights] if old_total else old_weights
        row[1:] = [f"{weight * 100:.2f}%" for weight in scaled + new_weights]

    return updated


# --------------------------------------------------------------------------- synchronisation

def _read_tab(spreadsheet, tab, rng="A1:D3000"):
    return spreadsheet.worksheet(tab).get(rng)


def _read_aliases(spreadsheet, tab):
    try:
        rows = _read_tab(spreadsheet, tab, "A1:B20000")
    except Exception:  # onglet absent : alias inchangés
        return None
    aliases = {}
    for r in rows[1:]:
        r = [str(c).strip() for c in r] + ["", ""]
        if r[0] and r[1]:
            aliases[r[0]] = r[1]
    return aliases


def pull(spreadsheet, tab=None):
    """Onglets config + alias -> config/config.json (et bootstrap.json si l'ID ou l'onglet changent).
    Renvoie la configuration de la ligue à jour (comme load_league)."""
    from scripts.config import load_league

    local_config_invalid = False
    try:
        current = load_config()
    except ValueError as exc:
        log.warning("[Config] Copie locale invalide (%s) : reconstruction depuis l'onglet Sheets.", exc)
        current = load_default_config()
        local_config_invalid = True
    boot_now = load_bootstrap()
    tab = tab or boot_now.get("config_tab") or "settings"
    cfg, boot = parse_rows(_read_tab(spreadsheet, tab), current, boot_now)
    aliases = _read_aliases(spreadsheet, cfg["league"].get("google_sheets", {}).get("alias_tab") or "players")
    if aliases:
        cfg["aliases"] = dict(sorted(aliases.items()))
    validate_runtime_config(cfg, boot)
    _check_tabs(spreadsheet, cfg["league"].get("google_sheets", {}))
    changed = []
    if local_config_invalid or cfg != current:
        save_config(cfg)
        changed.append("config.json")
    if boot != boot_now:
        save_bootstrap(boot)
        changed.append("bootstrap.json")
        log.warning("[Config] ID du classeur ou onglet settings modifié : pris en compte au prochain lancement.")
    log.info("[Config] %s", f"Mis à jour depuis l'onglet '{tab}' : {', '.join(changed)}." if changed
             else f"Onglet '{tab}' identique à la configuration locale.")
    return load_league()


def _check_tabs(spreadsheet, gs):
    """Signale les onglets de l'onglet settings qui n'existent pas dans le classeur."""
    try:
        existing = {ws.title for ws in spreadsheet.worksheets()}
    except Exception:  # classeur factice (tests) ou erreur réseau : contrôle ignoré
        return
    labels = {"alias_tab": "Players source", "projections_tab": "Projections", "board_tab": "Board"}
    if not gs.get("projections_spreadsheet_id"):
        missing = [f"{label} = {gs[key]}" for key, label in labels.items() if gs.get(key) and gs[key] not in existing]
    else:
        missing = [f"{label} = {gs[key]}" for key, label in labels.items()
                   if key != "projections_tab" and gs.get(key) and gs[key] not in existing]
    if missing:
        log.warning("[Config] Onglets introuvables dans le classeur (Sheets | Tab) : %s", ", ".join(missing))


def push(spreadsheet, tab=None, league=None):
    """Configuration locale -> onglets config et alias (remise à plat). league : ignoré (compatibilité)."""
    from scripts.sheets import write_tab

    cfg, boot = load_config(), load_bootstrap()
    tab = tab or boot.get("config_tab") or "settings"
    rows = build_rows(cfg, boot)
    write_tab(spreadsheet, tab, rows)
    alias_tab = cfg["league"].get("google_sheets", {}).get("alias_tab") or "players"
    write_tab(spreadsheet, alias_tab, [ALIAS_HEADER] + [[k, v] for k, v in sorted(cfg["aliases"].items())])
    try:
        fmt = {"textFormat": {"bold": True}, "backgroundColor": {"red": 0.85, "green": 0.9, "blue": 1}}
        ws = spreadsheet.worksheet(tab)
        ws.format("A1:D1", fmt)
        ws.format(f"C2:C{len(rows)}", {"backgroundColor": {"red": 1, "green": 0.97, "blue": 0.8}})
        ws.freeze(rows=1)
        spreadsheet.worksheet(alias_tab).format("A1:B1", fmt)
    except Exception as exc:  # la mise en forme est un bonus
        log.debug("Mise en forme de l'onglet settings ignorée : %s", exc)
    return rows


def migrate(spreadsheet, tab=None):
    """Ancien format (blocs) -> configuration locale, copie de sauvegarde, puis onglet au nouveau format."""
    from scripts import config_legacy
    from scripts.sheets import write_tab

    boot = load_bootstrap()
    tab = tab or boot.get("config_tab") or "settings"
    rows = spreadsheet.worksheet(tab).get("A1:Z600")
    cfg = load_config()
    league, settings, sources, grids = config_legacy.parse_rows(rows, cfg["league"], cfg["settings"], cfg["sources"])
    cfg.update(league=league, settings=settings, sources=sources)
    import csv
    import io

    for phase, text in grids.items():
        cfg["grids"][phase] = [r for r in csv.reader(io.StringIO(text), delimiter=";") if any(c.strip() for c in r)]
    gs = cfg["league"].get("google_sheets", {})
    gs.pop("draft_spreadsheet_id", None)
    gs.pop("config_tab", None)
    aliases = _read_aliases(spreadsheet, gs.get("alias_tab") or "players")
    if aliases:
        cfg["aliases"] = dict(sorted(aliases.items()))
    save_config(cfg)
    write_tab(spreadsheet, f"{tab}_old", rows)
    push(spreadsheet, tab)
    return tab


def main(argv=None):
    parser = argparse.ArgumentParser(description="Onglet config du classeur <-> configuration locale")
    parser.add_argument("command", choices=["pull", "push", "migrate", "check"])
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    from scripts.config import load_settings
    from scripts.sheets import open_spreadsheet

    boot = load_bootstrap()
    if not boot.get("spreadsheet_id"):
        log.error("ID du classeur absent de config/bootstrap.json (spreadsheet_id).")
        return 1
    book = open_spreadsheet(boot["spreadsheet_id"], load_settings())
    try:
        if args.command == "pull":
            pull(book)
        elif args.command == "check":
            parse_rows(_read_tab(book, boot.get("config_tab") or "settings"))
            print("Onglet config lisible, aucune erreur.")
        elif args.command == "push":
            rows = push(book)
            print(f"Onglet '{boot.get('config_tab')}' réécrit ({len(rows) - 1} lignes).")
        else:
            tab = migrate(book)
            print(f"Onglet '{tab}' converti au nouveau format (ancien contenu copié dans '{tab}_old').")
    except ValueError as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
