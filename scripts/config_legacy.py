"""Lecture de l'ANCIEN format de l'onglet config (blocs : paramètres, Ordre, Source, Grille...).

Sert uniquement à la migration vers le format actuel (python -m scripts.config_sheet migrate).
"""

import csv
import io
import json
import logging
import os
import tempfile


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
        active = _parse_bool(entry["active"] or "oui", f"Activée ({name})")
        settings.setdefault("sources", {})[name] = {"draft": active, "ros": active}
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


def parse_rows(rows, league, settings, sources):
    """Applique l'ancien onglet à des copies. Renvoie (league, settings, sources, grilles en texte CSV)."""
    league = json.loads(json.dumps(league))
    settings = json.loads(json.dumps(settings))
    sources = json.loads(json.dumps(sources))
    blocks = _blocks(rows)
    _parse_params([lines[0] for _, lines in blocks.get("params", [])], league, settings)
    if "managers" not in blocks:
        raise ValueError(f"Onglet config : ligne d'en-tête « {MANAGERS_HEADER} | Manager | Keeper 1... » introuvable.")
    _parse_managers(*blocks["managers"][0], league)
    if "sources" in blocks:
        _parse_sources(*blocks["sources"][0], sources, settings)
    grids = _parse_grids(blocks.get("grid", []), settings.get("active_season")) if "grid" in blocks else {}
    return league, settings, sources, grids


