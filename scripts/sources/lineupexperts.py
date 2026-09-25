"""Import des projections LineupExperts.

Le site est protégé par Cloudflare : pas de lecture automatique possible.
L'export se fait dans votre navigateur avec tools/lineupexperts_export.js
(voir tools/README.md), qui télécharge lineupexperts_<étape>_AAAA-MM-JJ.csv.
Déposer ce fichier dans data/imports/lineupexperts/, puis :

    python main.py --sources lineupexperts
    python -m scripts.sources.lineupexperts                       # fichier le plus récent
    python -m scripts.sources.lineupexperts chemin/vers/export.csv # fichier précis

Les stats sont des moyennes par match (converties en totaux avec GP).
"""

import logging
import sys

from scripts.sources.csv_source import CsvProjectionSource


class LineupExpertsSource(CsvProjectionSource):
    name = "lineupexperts"
    label = "LineupExperts"


def fetch_lineupexperts_projections(settings=None, file_path=None):
    return LineupExpertsSource(settings, file_path=file_path).run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from scripts.db import init_db

    init_db()
    fetch_lineupexperts_projections(file_path=sys.argv[1] if len(sys.argv) > 1 else None)
