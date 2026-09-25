"""Import des projections FanScout (export CSV depuis https://fanscout.pro/projections).

Déposer le fichier dans data/imports/fanscout/ sous le nom
fanscout_<étape>_AAAA-MM-JJ.csv (ex. fanscout_draft_2026-09-25.csv), puis :

    python main.py --sources fanscout
    python -m scripts.sources.fanscout                       # fichier le plus récent
    python -m scripts.sources.fanscout chemin/vers/table.csv  # fichier précis
"""

import logging
import sys

from scripts.sources.csv_source import CsvProjectionSource


class FanScoutSource(CsvProjectionSource):
    name = "fanscout"
    label = "FanScout"


def fetch_fanscout_projections(settings=None, file_path=None):
    return FanScoutSource(settings, file_path=file_path).run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from scripts.db import init_db

    init_db()
    fetch_fanscout_projections(file_path=sys.argv[1] if len(sys.argv) > 1 else None)
