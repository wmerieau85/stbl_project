"""Lecture et validation des grilles de pondération (onglet settings, sections « Grid <phase> | ... »).

Format : la grille telle qu'elle est dans Google Sheets (copier/coller ou export CSV).

    ;lt01;lt02;lt03;lt04;lt05
    site;fs26;fp26;cbs26;le26;dk26
    GP;22,50%;22,50%;10,00%;22,50%;22,50%
    MIN;20,00%;20,00%;20,00%;20,00%;20,00%
    STATS;20,00%;20,00%;20,00%;20,00%;20,00%

- 1re ligne : identifiants des emplacements (libres).
- Ligne « site » : code de la source, voir parse_code().
- Lignes GP / MIN / STATS : poids des 3 niveaux, chaque ligne doit faire 100 %.
- Lignes optionnelles pour une stat particulière, ex. « FT% » ou « STATS:BLK » :
  remplacent la ligne STATS pour cette seule stat.
- Séparateur ; , ou tabulation ; poids en « 22,50% », « 22.5% », « 0.225 » ou « 22.5 ».
"""

import csv
import io
import logging
import os
import re

from scripts.config import load_grid_rows, load_sources_config

log = logging.getLogger(__name__)

LEVELS = ("gp", "min", "stats")

# Stats pondérées au niveau STATS (clé = nom de colonne en base)
STAT_CATEGORIES = ("pts", "reb", "ast", "stl", "blk", "tov", "fg3m", "fga", "fta", "fg3a", "fgp", "ftp")

# Libellés acceptés dans les grilles pour cibler une stat
CATEGORY_ALIASES = {
    "pts": "pts", "points": "pts",
    "reb": "reb", "rb": "reb", "trb": "reb",
    "ast": "ast",
    "stl": "stl", "st": "stl",
    "blk": "blk", "bl": "blk",
    "tov": "tov", "to": "tov",
    "3pm": "fg3m", "fg3m": "fg3m", "3ptm": "fg3m", "tpm": "fg3m", "fg3": "fg3m",
    "fga": "fga", "fta": "fta", "3pa": "fg3a", "fg3a": "fg3a",
    "fg%": "fgp", "fgp": "fgp", "ft%": "ftp", "ftp": "ftp",
}

# Phases calculées, dans l'ordre : une grille peut reprendre le résultat d'une phase précédente
FINAL_PHASES = ("draft", "lt", "st")

CODE_RE = re.compile(r"^([a-z]+)(\d{2})?(?:\.([a-z0-9]+))?$")


class WeightsError(ValueError):
    pass


def season_from_year(yy):
    start = 2000 + int(yy)
    return f"{start}-{(start + 1) % 100:02d}"


def source_codes():
    """{code: libellé enregistré en base}, depuis la configuration des sources."""
    from scripts.sources import SOURCES

    sources_config = load_sources_config()
    codes = {}
    for name, cls in SOURCES.items():
        code = sources_config.get(name, {}).get("code")
        if code:
            codes[code] = cls.label
    return codes


def parse_code(code, phase, active_season):
    """Traduit un code de la ligne « site » en (source, saison, étape).

    - fs26      -> FanScout, saison 2026-27, étape draft (projections de pré-saison)
    - fp.ros    -> FantasyPros, saison active, étape ros
    - fp26.ros  -> FantasyPros, saison 2026-27, étape ros
    - fp.l30    -> FantasyPros, saison active, fenêtre l30 (quand la source existera)
    - draft     -> projections finales de la phase draft (utilisable en lt / st)
    - lt        -> projections finales de la phase lt (utilisable en st)
    """
    code = code.strip().lower()
    if code in FINAL_PHASES:
        if FINAL_PHASES.index(code) >= FINAL_PHASES.index(phase) if phase in FINAL_PHASES else False:
            raise WeightsError(f"Le code '{code}' ne peut pas être utilisé dans la grille {phase} "
                               f"(seulement une phase calculée avant : {', '.join(FINAL_PHASES[:FINAL_PHASES.index(phase)]) or 'aucune'}).")
        return {"code": code, "source": code, "season": active_season, "stage": code, "final": True}
    match = CODE_RE.match(code)
    if not match:
        raise WeightsError(f"Code source illisible : '{code}' (ex. fs26, fp.ros, fp.l30, draft)")
    site, yy, window = match.groups()
    codes = source_codes()
    if site not in codes:
        raise WeightsError(f"Code site inconnu : '{site}' (codes connus : {', '.join(sorted(codes))})")
    season = season_from_year(yy) if yy else active_season
    stage = window or ("draft" if yy else ("draft" if phase == "draft" else "ros"))
    source_label = codes[site]
    from scripts.sources import SOURCES

    allow_per_game_without_gp = any(
        cls.label == source_label and cls.per_game for cls in SOURCES.values()
    )
    return {"code": code, "source": source_label, "season": season, "stage": stage, "final": False,
            "allow_per_game_without_gp": allow_per_game_without_gp}


def parse_weight(text):
    text = (text or "").strip().replace("\u00a0", "").replace(" ", "")
    if not text:
        return 0.0
    percent = text.endswith("%")
    value = float(text.rstrip("%").replace(",", "."))
    if percent or value > 1:
        value /= 100.0
    if value < 0:
        raise WeightsError(f"Poids négatif : {text}")
    return value


def _read_rows(path):
    with open(path, "rb") as f:
        raw = f.read()
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    first = text.splitlines()[0] if text else ""
    delimiter = "\t" if "\t" in first else (";" if ";" in first else ",")
    return [row for row in csv.reader(io.StringIO(text), delimiter=delimiter) if any(c.strip() for c in row)]


def _level_of(label):
    key = label.strip().lower()
    if key in ("gp", "g", "games"):
        return "gp", None
    if key in ("min", "mpg", "minutes"):
        return "min", None
    if key in ("stats", "stat"):
        return "stats", None
    if key.startswith("stats:") or key.startswith("stats."):
        key = key[6:]
    if key in CATEGORY_ALIASES:
        return "stats", CATEGORY_ALIASES[key]
    return None, None


def load_grid(phase, active_season, path=None):
    """Retourne {"phase", "path", "slots": [{slot, code, source, season, stage, final,
    weights: {"gp": w, "min": w, "stats": {cat: w}}}]} avec des poids normalisés (somme = 1)."""
    if path:
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        rows = _read_rows(path)
    else:
        rows = load_grid_rows(phase)
        if rows is None:
            raise FileNotFoundError(f"grille {phase} (onglet settings, sections Grid {phase})")
        rows = [[str(c) for c in r] for r in rows if any(str(c).strip() for c in r)]
        path = f"Grille {phase}"
    if len(rows) < 3:
        raise WeightsError(f"{path} : grille incomplète.")

    slot_ids = [c.strip() for c in rows[0][1:]]
    site_row = next((r for r in rows[1:] if r and r[0].strip().lower() in ("site", "source", "code")), None)
    if site_row is None:
        raise WeightsError(f"{path} : ligne « site » absente.")

    slots = []
    for i, code in enumerate(site_row[1:]):
        slot = {"slot": slot_ids[i] if i < len(slot_ids) and slot_ids[i] else f"slot{i + 1}",
                "code": code.strip(), "weights": {"gp": 0.0, "min": 0.0, "stats": {}}}
        slots.append(slot)

    level_rows = {}
    category_rows = {}
    for row in rows[1:]:
        if row is site_row or not row or not row[0].strip():
            continue
        level, category = _level_of(row[0])
        if level is None:
            log.warning("[Pondération %s] Ligne ignorée : '%s'", phase, row[0])
            continue
        values = [parse_weight(row[i + 1]) if i + 1 < len(row) else 0.0 for i in range(len(slots))]
        if category:
            category_rows[category] = values
        else:
            level_rows[level] = values

    for level in LEVELS:
        if level not in level_rows:
            raise WeightsError(f"{path} : ligne {level.upper()} absente.")

    def check(values, label):
        total = sum(values)
        if abs(total - 1) > 0.005:
            raise WeightsError(f"{path} : la ligne {label} fait {total:.2%} au lieu de 100 %.")
        return [v / total for v in values]

    gp = check(level_rows["gp"], "GP")
    mins = check(level_rows["min"], "MIN")
    stats = check(level_rows["stats"], "STATS")
    per_category = {cat: stats for cat in STAT_CATEGORIES}
    for category, values in category_rows.items():
        per_category[category] = check(values, category.upper())

    active = []
    for i, slot in enumerate(slots):
        slot["weights"]["gp"] = gp[i]
        slot["weights"]["min"] = mins[i]
        slot["weights"]["stats"] = {cat: per_category[cat][i] for cat in STAT_CATEGORIES}
        used = gp[i] > 0 or mins[i] > 0 or any(v > 0 for v in slot["weights"]["stats"].values())
        if not slot["code"]:
            if used:
                raise WeightsError(f"{path} : l'emplacement {slot['slot']} a des poids mais pas de code site.")
            continue
        if not used:
            log.info("[Pondération %s] %s (%s) : aucun poids, ignoré.", phase, slot["slot"], slot["code"])
            continue
        slot.update(parse_code(slot["code"], phase, active_season))
        active.append(slot)

    return {"phase": phase, "path": path, "slots": active}
