"""Base des sources alimentées par un fichier CSV déposé manuellement.

Le fichier est cherché dans le dossier `import_dir` défini dans mappings.json,
selon le motif propre à l'étape (`files.draft`, `files.ros`...). Si plusieurs
fichiers correspondent, le plus récent (d'après le nom, puis la date de
modification) est utilisé. Un chemin précis peut aussi être imposé.
"""

import csv
import glob
import io
import logging
import os

from scripts.config import BASE_DIR
from scripts.sources.base import ProjectionSource, parse_number

log = logging.getLogger(__name__)

ENCODINGS = ("utf-8-sig", "cp1252")
# Au-delà, une valeur de points ne peut pas être une moyenne par match
PER_GAME_MAX_PTS = 100


def read_text(path):
    """Lit un fichier texte en UTF-8 (avec ou sans BOM), à défaut en cp1252 (exports Windows)."""
    with open(path, "rb") as f:
        raw = f.read()
    for encoding in ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Encodage non reconnu : {path}")


def detect_delimiter(text):
    sample = text[:5000]
    try:
        return csv.Sniffer().sniff(sample, delimiters=";,\t|").delimiter
    except csv.Error:
        return ";" if sample.count(";") > sample.count(",") else ","


class CsvProjectionSource(ProjectionSource):
    """Source CSV générique : tout se paramètre dans mappings.json."""

    def __init__(self, settings=None, http=None, file_path=None):
        super().__init__(settings, http)
        self.file_path = file_path

    # --- recherche du fichier ------------------------------------------------
    def import_dir(self):
        return os.path.join(BASE_DIR, self.mapping.get("import_dir", os.path.join("data", "imports", self.name)))

    def resolve_file(self):
        if self.file_path:
            return self.file_path if os.path.exists(self.file_path) else None
        pattern = self.mapping.get("files", {}).get(self.stage)
        if not pattern:
            log.error("[%s] Aucun motif de fichier pour l'étape '%s' dans mappings.json.", self.label, self.stage)
            return None
        candidates = glob.glob(os.path.join(self.import_dir(), pattern))
        if not candidates:
            log.error(
                "[%s] Aucun fichier '%s' dans %s. Déposez l'export CSV à cet endroit.",
                self.label, pattern, self.import_dir(),
            )
            return None
        # Nom au format <source>_<étape>_AAAA-MM-JJ.csv : le tri par nom donne le plus récent
        return max(candidates, key=lambda p: (os.path.basename(p), os.path.getmtime(p)))

    def documents(self):
        path = self.resolve_file()
        if path is None:
            return []
        log.info("[%s] Lecture du fichier %s", self.label, path)
        return [(path, read_text(path), None)]

    # --- lecture du CSV ------------------------------------------------------
    def _detect_per_game(self, rows):
        mode = self.mapping.get("stat_mode", "auto")
        if mode in ("totals", "per_game"):
            return mode == "per_game"
        pts_col = next((src for src, std in self.mapping["columns"].items() if std == "pts"), None)
        values = [parse_number(r.get(pts_col)) for r in rows] if pts_col else []
        values = [v for v in values if v is not None]
        per_game = bool(values) and max(values) < PER_GAME_MAX_PTS
        log.info("[%s] Stats détectées : %s.", self.label, "par match" if per_game else "totaux sur la saison")
        return per_game

    def parse_page(self, text, _context):
        reader = csv.DictReader(io.StringIO(text), delimiter=detect_delimiter(text))
        rows = [
            {(k or "").strip(): (v or "").strip() for k, v in row.items() if k is not None}
            for row in reader
        ]
        if not rows:
            return []

        player_col = self.mapping.get("player_column", "Player")
        team_col = self.mapping.get("team_column")
        pos_col = self.mapping.get("positions_column")
        missing = [c for c in [player_col, team_col, pos_col] if c and c not in rows[0]]
        if missing:
            log.error("[%s] Colonnes absentes du CSV : %s (vérifier mappings.json).", self.label, ", ".join(missing))
            return []
        unknown = sorted(set(rows[0]) - set(self.mapping["columns"]) - {player_col, team_col, pos_col})
        if unknown:
            log.debug("[%s] Colonnes ignorées : %s", self.label, ", ".join(unknown))

        self.per_game = self._detect_per_game(rows)
        return [
            {
                "player": row.get(player_col, ""),
                "team": row.get(team_col, "") if team_col else "",
                "positions": [p for p in row.get(pos_col, "").replace("/", ",").split(",") if p] if pos_col else [],
                "stats": row,
            }
            for row in rows
            if row.get(player_col)
        ]
