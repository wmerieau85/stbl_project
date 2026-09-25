"""Import des projections DraftKick (export CSV).

Déposer le fichier dans data/imports/draftkick/ sous le nom
draftkick_<étape>_AAAA-MM-JJ.csv (ex. draftkick_ros_2026-09-25.csv), puis :

    python main.py --sources draftkick
    python -m scripts.sources.draftkick                        # fichier le plus récent
    python -m scripts.sources.draftkick chemin/vers/export.csv  # fichier précis

Particularités de l'export, gérées dans mappings.json :
- deux colonnes "Team" : la 1re est l'équipe fantasy (vide), la 2e l'équipe NBA ("Team_2") ;
- ADP 999 = joueur non drafté (enregistré vide) ;
- cases de stats vides = 0 ;
- pas de minutes ni de tentatives de tirs (FGA/FTA) : seuls les pourcentages sont fournis.
"""

import logging
import sys

from scripts.sources.csv_source import CsvProjectionSource


class DraftKickSource(CsvProjectionSource):
    name = "draftkick"
    label = "DraftKick"


def fetch_draftkick_projections(settings=None, file_path=None):
    return DraftKickSource(settings, file_path=file_path).run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from scripts.db import init_db

    init_db()
    fetch_draftkick_projections(file_path=sys.argv[1] if len(sys.argv) > 1 else None)
