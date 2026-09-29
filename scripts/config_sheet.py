"""Onglet « config » du classeur <-> fichiers du dossier config/.

L'onglet regroupe des blocs, repérés par le texte de leur 1re cellule (colonne A) ; un bloc se
termine à la première ligne vide, l'ordre des blocs et des lignes est libre :

1. paramètres « Section | Paramètre | Valeur | Aide » -> league.json et settings.json ;
2. « Ordre | Manager | Équipe Yahoo | Keeper 1 | Keeper 2... » -> ordre de draft, équipes, keepers ;
3. « Source | Code | Activée | Étape | URL ou fichier | Dossier d'import » -> sources.json
   (+ sources activées et fenêtres de stats dans settings.json) ;
4. « Grille draft », « Grille lt », « Grille st »... -> config/weights/<phase>.csv (même disposition
   que le fichier CSV : ligne des cases, ligne « site », puis GP, MIN, STATS...).

Les alias de noms de joueurs (player_aliases.json) sont dans un onglet à part (config_alias par
défaut), vu leur nombre : « Nom lu dans une source | Nom de référence ».

    python -m scripts.draft config-push   # réécrit les onglets à partir des fichiers
    python -m scripts.draft config-pull   # met à jour les fichiers à partir des onglets

L'identifiant du classeur et le chemin du compte de service restent dans les fichiers JSON
(il faut les connaître pour pouvoir lire l'onglet). Les correspondances de colonnes des sources
(sources.json > columns) restent techniques et ne sont pas exposées.
"""

import csv
import glob
import io
import json
import logging
import os
import tempfile

from scripts.config import (ALIASES_PATH, LEAGUE_PATH, SETTINGS_PATH, SOURCES_PATH, WEIGHTS_DIR,
                            _read_json)

log = logging.getLogger(__name__)

PARAMS_HEADER = ["Section", "Paramètre", "Valeur", "Aide"]
YAHOO_TEAM_HEADER = "Équipe Yahoo"
MANAGERS_HEADER = "Ordre"
SOURCES_HEADER = ["Source", "Code", "Activée", "Étape", "URL ou fichier", "Dossier d'import"]
GRID_PREFIX = "Grille "
ALIAS_HEADER = ["Nom lu dans une source", "Nom de référence"]
MAIN_STAGES = ("draft", "ros")

# (section, libellé, fichier, chemin, type, aide)
PARAMS = [
    ("Général", "Saison active", "settings", "active_season", "str", "ex. 2026-27"),
    ("Général", "Étape active", "settings", "active_stage", "choice:draft,ros",
     "draft (avant la saison) ou ros (en cours de saison : phases lt et st)"),
    ("Ligue", "Format", "league", "format", "choice:roto,h2h", "roto ou h2h"),
    ("Ligue", "Plateforme", "league", "platform", "str", "yahoo (positions Yahoo)"),
    ("Ligue", "Nombre d'équipes", "league", "teams", "int", "doit correspondre au nombre de managers (bloc Ordre)"),
    ("Ligue", "ID de la ligue Yahoo", "league", "yahoo.league_id", "optstr",
     "basketball.fantasysports.yahoo.com/nba/<ID> (change chaque saison)"),
    ("Roster", "G", "league", "roster.G", "int", "postes titulaires Guard"),
    ("Roster", "F", "league", "roster.F", "int", "postes titulaires Forward"),
    ("Roster", "C", "league", "roster.C", "int", "postes titulaires Center"),
    ("Roster", "UTIL", "league", "roster.UTIL", "int", "postes titulaires Util"),
    ("Roster", "BN", "league", "roster.BN", "int", "banc"),
    ("Roster", "IL", "league", "roster.IL", "int", "blessés"),
    ("Catégories", "FG%", "league", "categories.fgp", "float", "poids (0 = catégorie ignorée)"),
    ("Catégories", "3PM", "league", "categories.fg3m", "float", ""),
    ("Catégories", "FT%", "league", "categories.ftp", "float", ""),
    ("Catégories", "REB", "league", "categories.reb", "float", ""),
    ("Catégories", "AST", "league", "categories.ast", "float", ""),
    ("Catégories", "STL", "league", "categories.stl", "float", ""),
    ("Catégories", "BLK", "league", "categories.blk", "float", ""),
    ("Catégories", "TO", "league", "categories.tov", "float", ""),
    ("Catégories", "PTS", "league", "categories.pts", "float", ""),
    ("Matchs", "Matchs par poste titulaire", "league", "games.per_slot", "int", "82 x postes titulaires = plafond"),
    ("Matchs", "Alignements", "league", "games.lineup", "choice:daily,weekly", "daily ou weekly"),
    ("Valorisation", "Matchs minimum", "league", "zscore.min_gp", "int",
     "z-scores : matchs projetés pour entrer dans le groupe de référence"),
    ("Valorisation", "Itérations", "league", "zscore.iterations", "int", "z-scores : recalculs du groupe de référence"),
    ("Projections", "Onglet des projections", "league", "google_sheets.projections_tab", "str",
     "toutes les phases (colonne A = phase), écrit par push-projections / --push-sheet"),
    ("Projections", "Colonne de début", "league", "google_sheets.projections_start_col", "str",
     "les colonnes à droite du bloc (formules) ne sont pas touchées"),
    ("Draft", "Type", "league", "draft.type", "choice:snake", "snake"),
    ("Draft", "Nombre de tours", "league", "draft.rounds", "int", "tours de keepers compris"),
    ("Draft", "Tours des keepers", "league", "draft.keeper_rounds", "intlist", "ex. 1, 2 (vide = keepers en plus des tours)"),
    ("Draft", "Mon équipe", "league", "draft.my_team", "str", "nom du manager, identique au bloc Ordre"),
    ("Draft", "Source des choix", "league", "draft.picks_source", "choice:yahoo,sheet",
     "yahoo (lecture en direct) ou sheet (saisie dans l'onglet de saisie des choix)"),
    ("Draft", "Onglet de saisie des choix", "league", "google_sheets.picks_tab", "str", "ex. draft_res"),
    ("Draft", "Plage des choix", "league", "google_sheets.picks_range", "str", "tour, choix, clé, joueur"),
    ("Draft", "Recopier les choix Yahoo dans l'onglet de saisie", "league", "google_sheets.write_picks_to_sheet",
     "bool", "oui / non (seuls les choix faits dans Yahoo sont recopiés)"),
    ("Draft", "Onglet de la recommandation", "league", "google_sheets.reco_tab", "str", "réécrit à chaque recalcul"),
    ("Draft", "Fréquence de veille (secondes)", "league", "google_sheets.poll_seconds", "int",
     "watch : lecture des choix toutes les N secondes"),
    ("Draft", "Candidats évalués", "league", "draft.candidates", "int", "joueurs testés à chaque recalcul"),
    ("Draft", "Simulations", "league", "draft.simulations", "int", "tirages de la suite de la draft par candidat"),
    ("Draft", "Bruit ADP", "league", "draft.adp_noise", "float", "0,15 = les managers s'écartent de ~15 % de l'ADP"),
    ("Saison", "Source des effectifs", "league", "season.roster_source", "choice:yahoo,sheet",
     "yahoo (effectifs réels) ou sheet (onglet ci-dessous, ex. simulation de draft)"),
    ("Saison", "Onglet des effectifs (sheet)", "league", "season.roster_tab", "str", "colonnes Player et Team en 1re ligne"),
    ("Saison", "Horizon court terme (jours)", "league", "season.st_days", "int", "jours projetés avec la phase st, ensuite lt"),
    ("Saison", "Phase long terme", "league", "season.lt_phase", "str", "grille utilisée pour le reste de la saison"),
    ("Saison", "Phase court terme", "league", "season.st_phase", "str", "grille utilisée pour l'horizon court terme"),
    ("Saison", "Simulations", "league", "season.simulations", "int", "tirages pour les probabilités de classement"),
    ("Saison", "Onglet saison", "league", "google_sheets.season_tab", "str", "classement réel et projeté, mon effectif"),
    ("Import", "Délai max par page (s)", "settings", "http.timeout", "int", ""),
    ("Import", "Tentatives", "settings", "http.retries", "int", "nouvelles tentatives en cas d'échec"),
    ("Import", "Pause entre pages (s)", "settings", "http.pause_seconds", "float", ""),
    ("Export", "Séparateur CSV", "settings", "export.delimiter", "str", "; pour Excel en français"),
    ("Export", "Séparateur décimal", "settings", "export.decimal", "str", ","),
    ("Onglets", "Onglet des alias de joueurs", "league", "google_sheets.alias_tab", "str",
     "noms lus dans les sources -> nom de référence"),
]

# anciens libellés (onglets écrits avant la réorganisation) -> (section, libellé) actuels,
# avec remplacement des anciennes valeurs par défaut
OLD_LABELS = {
    ("z-scores", "matchs minimum"): (("Valorisation", "Matchs minimum"), {}),
    ("z-scores", "itérations"): (("Valorisation", "Itérations"), {}),
    ("sheets", "onglet des choix"): (("Draft", "Onglet de saisie des choix"), {}),
    ("sheets", "plage des choix"): (("Draft", "Plage des choix"), {}),
    ("sheets", "onglet des recommandations"): (("Draft", "Onglet de la recommandation"), {"reco": "draft_reco"}),
    ("sheets", "onglet des projections"): (("Projections", "Onglet des projections"), {"draft_bdd": "proj"}),
    ("sheets", "colonne de début des projections"): (("Projections", "Colonne de début"), {}),
    ("sheets", "onglet saison"): (("Saison", "Onglet saison"), {}),
    ("sheets", "veille (secondes)"): (("Draft", "Fréquence de veille (secondes)"), {}),
    ("sheets", "recopier les choix yahoo dans draft_res"):
        (("Draft", "Recopier les choix Yahoo dans l'onglet de saisie"), {}),
}


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
    if kind == "intlist":
        return ", ".join(str(v) for v in value)
    if kind == "bool":
        return "oui" if value else "non"
    return value


def _parse(raw, kind, label):
    text = str(raw).strip() if raw is not None else ""
    if kind == "intlist":
        return [int(float(v)) for v in text.replace(";", ",").split(",") if v.strip()]
    if kind == "optstr":
        return text
    if kind == "bool":
        return _parse_bool(text, label)
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


def _parse_bool(text, label):
    text = str(text or "").strip().lower()
    if text in ("oui", "o", "yes", "true", "vrai", "1"):
        return True
    if text in ("non", "n", "no", "false", "faux", "0"):
        return False
    raise ValueError(f"Onglet config : « {label} » = '{text}', attendu : oui ou non.")


def _clean(row, width=0):
    cells = [str(c).strip() if c is not None else "" for c in row]
    return cells + [""] * max(0, width - len(cells))


def _block_kind(first_cell):
    text = first_cell.strip().lower()
    if text == MANAGERS_HEADER.lower():
        return "managers"
    if text == SOURCES_HEADER[0].lower():
        return "sources"
    if text.startswith(GRID_PREFIX.lower()) and len(text) > len(GRID_PREFIX):
        return "grid"
    return None


def _blocks(rows):
    """{kind: [(entête, lignes)]} ; un bloc se termine à la 1re ligne vide ou au bloc suivant."""
    blocks, current = {}, None
    for raw in rows:
        r = _clean(raw)
        if not any(r):
            current = None
            continue
        kind = _block_kind(r[0])
        if kind:
            current = (r, [])
            blocks.setdefault(kind, []).append(current)
        elif current is not None:
            current[1].append(r)
        else:
            blocks.setdefault("params", []).append((None, [r]))
    return blocks


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def save_league(league):
    _write_json(LEAGUE_PATH, league)


# --------------------------------------------------------------------------- lignes de l'onglet

def _grid_phases():
    return sorted(os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(WEIGHTS_DIR, "*.csv")))


def _read_grid_file(phase):
    with open(os.path.join(WEIGHTS_DIR, f"{phase}.csv"), encoding="utf-8-sig", newline="") as fh:
        return [row for row in csv.reader(fh, delimiter=";")]


def _source_rows(sources, settings):
    rows = [SOURCES_HEADER]
    enabled = settings.get("sources", {}) or {}
    for name, conf in sources.items():
        stages = conf.get("urls") or conf.get("files") or {}
        active = "oui" if enabled.get(name, True) else "non"
        for stage, target in stages.items():
            rows.append([name, conf.get("code", ""), active, stage, target, conf.get("import_dir", "")])
    return rows


def build_rows(league, settings=None, sources=None, phases=None):
    settings = settings if settings is not None else _read_json(SETTINGS_PATH)
    sources = sources if sources is not None else _read_json(SOURCES_PATH)
    files = {"league": league, "settings": settings}
    rows = [PARAMS_HEADER]
    for section, label, target, path, kind, help_text in PARAMS:
        rows.append([section, label, _display(_get(files[target], path), kind), help_text])
    rows.append([])

    draft = league.get("draft", {})
    keepers = draft.get("keepers", {}) or {}
    n_keep = max([2] + [len(v or []) for v in keepers.values()])
    yahoo_teams = (league.get("yahoo", {}) or {}).get("teams", {}) or {}
    rows.append([MANAGERS_HEADER, "Manager", YAHOO_TEAM_HEADER] + [f"Keeper {i}" for i in range(1, n_keep + 1)])
    for i, team in enumerate(draft.get("order", []), 1):
        names = list(keepers.get(team, []) or [])
        rows.append([i, team, yahoo_teams.get(team, "")] + names + [""] * (n_keep - len(names)))
    rows.append([])

    rows += _source_rows(sources, settings)
    for phase in (phases if phases is not None else _grid_phases()):
        rows.append([])
        rows.append([f"{GRID_PREFIX}{phase}"])
        rows += [r for r in _read_grid_file(phase) if any(c.strip() for c in r)]
    return rows


# --------------------------------------------------------------------------- lecture de l'onglet

def _parse_params(lines, league, settings):
    files = {"league": league, "settings": settings}
    by_label = {(s.lower(), l.lower()): (target, path, kind, l) for s, l, target, path, kind, _ in PARAMS}
    old, new = [], []
    for r in lines:
        r = r + ["", "", ""]
        key = (r[0].lower(), r[1].lower())
        if key in by_label:
            new.append((by_label[key], r[2]))
        elif key in OLD_LABELS:
            (section, label), migrate = OLD_LABELS[key]
            value = migrate.get(r[2], r[2])
            old.append((by_label[(section.lower(), label.lower())], value))
    for (target, path, kind, label), value in old + new:   # les libellés actuels l'emportent
        _set(files[target], path, _parse(value, kind, label))


def _parse_managers(header, lines, league):
    header = [h.lower() for h in header]
    yahoo_col = header.index(YAHOO_TEAM_HEADER.lower()) if YAHOO_TEAM_HEADER.lower() in header else None
    keeper_cols = [j for j, h in enumerate(header) if h.startswith("keeper")]
    order, keepers, yahoo_teams = [], {}, {}
    for r in lines:
        r = r + [""] * (len(header) + 2)
        if not r[1]:
            continue
        team = r[1]
        try:
            rank = float(r[0].replace(",", ".")) if r[0] else len(order) + 1
        except ValueError:
            raise ValueError(f"Onglet config : ordre '{r[0]}' illisible pour {team}.") from None
        order.append((rank, team))
        keepers[team] = [r[j] for j in keeper_cols if r[j]]
        if yahoo_col is not None and r[yahoo_col]:
            yahoo_teams[team] = r[yahoo_col]
    order = [team for _, team in sorted(order, key=lambda x: x[0])]
    if len(order) != int(league["teams"]):
        raise ValueError(f"Onglet config : {len(order)} managers pour {league['teams']} équipes.")
    league["draft"]["order"] = order
    league["draft"]["keepers"] = keepers
    if yahoo_col is not None:
        league.setdefault("yahoo", {})["teams"] = yahoo_teams
    if league["draft"].get("my_team") not in order:
        raise ValueError(f"Onglet config : « Mon équipe » ({league['draft'].get('my_team')}) absente des managers.")


def _parse_sources(header, lines, sources, settings):
    col = {h.lower(): j for j, h in enumerate(header)}
    need = [h.lower() for h in SOURCES_HEADER[:5]]
    missing = [h for h in need if h not in col]
    if missing:
        raise ValueError(f"Onglet config, bloc Source : colonnes absentes : {', '.join(missing)}.")
    seen = {}
    for r in lines:
        r = r + [""] * len(header)
        name = r[col["source"]]
        if not name:
            continue
        if name not in sources:
            log.warning("[Config] Source inconnue ignorée : %s (à déclarer dans sources.json).", name)
            continue
        entry = seen.setdefault(name, {"code": r[col["code"]], "active": r[col["activée"]], "stages": {},
                                       "dir": r[col["dossier d'import"]] if "dossier d'import" in col else None})
        if r[col["étape"]] and r[col["url ou fichier"]]:
            entry["stages"][r[col["étape"]]] = r[col["url ou fichier"]]
    codes = {}
    for name, entry in seen.items():
        conf = sources[name]
        if entry["code"]:
            if entry["code"] in codes:
                raise ValueError(f"Onglet config : code source « {entry['code']} » utilisé par {codes[entry['code']]} "
                                 f"et {name}.")
            codes[entry["code"]] = name
            conf["code"] = entry["code"]
        key = "urls" if "urls" in conf else "files"
        conf[key] = entry["stages"]
        if entry["dir"] is not None and "import_dir" in conf and entry["dir"]:
            conf["import_dir"] = entry["dir"]
        settings.setdefault("sources", {})[name] = _parse_bool(entry["active"] or "oui", f"Activée ({name})")
        if key == "urls":
            windows = [s for s in entry["stages"] if s not in MAIN_STAGES]
            if windows or name in (settings.get("stats_windows") or {}):
                settings.setdefault("stats_windows", {})[name] = windows


def _grid_text(lines):
    width = max(len(r) for r in lines)
    while width > 1 and not any(len(r) >= width and r[width - 1] for r in lines):
        width -= 1
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\n")
    for r in lines:
        writer.writerow((r + [""] * width)[:width])
    return out.getvalue()


def _parse_grids(blocks, season):
    from scripts.weighting.weights import WeightsError, load_grid

    grids = {}
    for header, lines in blocks:
        phase = header[0][len(GRID_PREFIX):].strip()
        if not lines:
            raise ValueError(f"Onglet config : grille {phase} vide.")
        text = _grid_text(lines)
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as tmp:
            tmp.write(text)
        try:
            load_grid(phase, season, path=tmp.name)   # contrôle avant d'écraser le fichier
        except (WeightsError, ValueError) as exc:
            raise ValueError(f"Onglet config, grille {phase} : {str(exc).replace(tmp.name + ' : ', '')}") from None
        finally:
            os.unlink(tmp.name)
        grids[phase] = text
    return grids


def parse_rows(rows, league, settings=None, sources=None):
    """Applique l'onglet à des copies des fichiers. Renvoie (league, settings, sources, grilles)."""
    league = json.loads(json.dumps(league))
    settings = json.loads(json.dumps(settings if settings is not None else _read_json(SETTINGS_PATH)))
    sources = json.loads(json.dumps(sources if sources is not None else _read_json(SOURCES_PATH)))
    blocks = _blocks(rows)
    _parse_params([lines[0] for _, lines in blocks.get("params", [])], league, settings)
    if "managers" not in blocks:
        raise ValueError(f"Onglet config : ligne d'en-tête « {MANAGERS_HEADER} | Manager | Keeper 1... » introuvable.")
    _parse_managers(*blocks["managers"][0], league)
    if "sources" in blocks:
        _parse_sources(*blocks["sources"][0], sources, settings)
    grids = _parse_grids(blocks.get("grid", []), settings.get("active_season")) if "grid" in blocks else {}
    return league, settings, sources, grids


# --------------------------------------------------------------------------- alias

def alias_tab(league):
    return (league.get("google_sheets", {}) or {}).get("alias_tab") or "config_alias"


def _pull_aliases(spreadsheet, tab):
    try:
        rows = spreadsheet.worksheet(tab).get("A1:B20000")
    except Exception:   # onglet pas encore créé : rien à relire
        return False
    aliases = {}
    for r in rows[1:]:
        r = _clean(r, 2)
        if r[0] and r[1]:
            aliases[r[0]] = r[1]
    if not aliases:
        return False
    current = _read_json(ALIASES_PATH) if os.path.exists(ALIASES_PATH) else {}
    if aliases == current:
        return False
    _write_json(ALIASES_PATH, dict(sorted(aliases.items())))
    return True


# --------------------------------------------------------------------------- synchronisation

def pull(spreadsheet, tab):
    """Onglets config (+ alias) -> fichiers du dossier config/. Renvoie league à jour."""
    rows = spreadsheet.worksheet(tab).get("A1:Z600")
    league_now = _read_json(LEAGUE_PATH)
    settings_now = _read_json(SETTINGS_PATH)
    sources_now = _read_json(SOURCES_PATH)
    league, settings, sources, grids = parse_rows(rows, league_now, settings_now, sources_now)
    changed = []
    if league != league_now:
        save_league(league)
        changed.append("league.json")
    if settings != settings_now:
        _write_json(SETTINGS_PATH, settings)
        changed.append("settings.json")
    if sources != sources_now:
        _write_json(SOURCES_PATH, sources)
        changed.append("sources.json")
    for phase, text in grids.items():
        path = os.path.join(WEIGHTS_DIR, f"{phase}.csv")
        old = open(path, encoding="utf-8-sig").read() if os.path.exists(path) else None
        if old is None or _normalize_grid(old) != _normalize_grid(text):
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
            changed.append(f"weights/{phase}.csv")
    if _pull_aliases(spreadsheet, alias_tab(league)):
        changed.append("player_aliases.json")
    if changed:
        log.info("[Config] Mis à jour depuis l'onglet '%s' : %s.", tab, ", ".join(changed))
    else:
        log.info("[Config] Onglet '%s' identique aux fichiers de config.", tab)
    return league


def _normalize_grid(text):
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=";") if any(c.strip() for c in r)]
    return [[c.strip() for c in r] for r in rows]


def push(spreadsheet, tab, league):
    from scripts.sheets import write_tab

    rows = build_rows(league)
    write_tab(spreadsheet, tab, rows)
    aliases = _read_json(ALIASES_PATH) if os.path.exists(ALIASES_PATH) else {}
    write_tab(spreadsheet, alias_tab(league), [ALIAS_HEADER] + [[k, v] for k, v in sorted(aliases.items())])
    try:
        ws = spreadsheet.worksheet(tab)
        header_fmt = {"textFormat": {"bold": True}, "backgroundColor": {"red": 0.85, "green": 0.9, "blue": 1}}
        ws.format("A1:D1", header_fmt)
        params_end = next(i for i, r in enumerate(rows, 1) if not r)
        ws.format(f"C2:C{params_end - 1}", {"backgroundColor": {"red": 1, "green": 0.97, "blue": 0.8}})
        for i, r in enumerate(rows, 1):
            if r and isinstance(r[0], str) and _block_kind(r[0]):
                ws.format(f"A{i}:J{i}", header_fmt)
        ws.freeze(rows=1)
        spreadsheet.worksheet(alias_tab(league)).format("A1:B1", header_fmt)
    except Exception as exc:  # la mise en forme est un bonus
        log.debug("Mise en forme de l'onglet config ignorée : %s", exc)
