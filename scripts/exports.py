"""Exports CSV (format réglé dans settings.json > export : séparateur et décimale).

    python -m scripts.exports                 # projections brutes de la saison/étape actives
    python -m scripts.exports --stage ros     # autre étape
"""

import logging
import os

from scripts.config import EXPORTS_DIR, load_settings
from scripts.db import PROJECTION_COLUMNS, get_connection

log = logging.getLogger(__name__)


def write_csv(filename, columns, rows, settings=None):
    """Écrit exports/<filename> (UTF-8 avec BOM pour Excel) et retourne son chemin."""
    settings = settings or load_settings()
    export = settings.get("export", {})
    delimiter = export.get("delimiter", ";")
    decimal = export.get("decimal", ",")

    def fmt(value):
        if value is None:
            return ""
        if isinstance(value, float):
            text = repr(value)
            return text.replace(".", decimal) if decimal != "." else text
        text = str(value)
        if delimiter in text or '"' in text or "\n" in text:
            return '"' + text.replace('"', '""') + '"'
        return text

    os.makedirs(EXPORTS_DIR, exist_ok=True)
    path = os.path.join(EXPORTS_DIR, filename)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(delimiter.join(columns) + "\r\n")
        for row in rows:
            f.write(delimiter.join(fmt(v) for v in row) + "\r\n")
    return path


RAW_EXPORT_COLUMNS = ["player_id", "canonical_name"] + PROJECTION_COLUMNS + ["imported_at"]


def export_raw_projections(season=None, stage=None, settings=None):
    """Écrit exports/raw_<étape>_<saison>.csv : toutes les sources, une ligne par joueur et source."""
    settings = settings or load_settings()
    season = season or settings["active_season"]
    stage = stage or settings["active_stage"]
    columns = ", ".join(f"r.{c}" if c not in ("player_id", "canonical_name") else
                        ("r.player_id" if c == "player_id" else "p.canonical_name")
                        for c in RAW_EXPORT_COLUMNS)
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT {columns} FROM raw_projections r LEFT JOIN players p ON p.player_id = r.player_id "
            "WHERE r.season=? AND r.stage=? "
            "ORDER BY COALESCE(p.canonical_name, r.player), r.source",
            (season, stage),
        ).fetchall()
    path = write_csv(f"raw_{stage}_{season}.csv", RAW_EXPORT_COLUMNS, rows, settings)
    log.info("[Export] %d lignes brutes -> %s", len(rows), path)
    return path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Export CSV des projections brutes")
    parser.add_argument("--season")
    parser.add_argument("--stage", choices=["draft", "ros"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    export_raw_projections(args.season, args.stage)
