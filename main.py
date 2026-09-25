"""Pipeline STBL : import des projections puis rapprochement des joueurs.

Exemples :
    python main.py                        # sources et étape définies dans config/settings.json
    python main.py --stage ros            # force les projections "rest of season"
    python main.py --sources cbs          # une seule source
    python main.py --file fanscout=C:/chemin/table.csv   # fichier CSV précis
"""

import argparse
import logging
import sys

from scripts.config import load_settings
from scripts.db import init_db
from scripts.http_client import HttpClient
from scripts.player_linker import link_players
from scripts.sources import SOURCES
from scripts.sources.csv_source import CsvProjectionSource

log = logging.getLogger("stbl")


def parse_args():
    parser = argparse.ArgumentParser(description="Pipeline de projections fantasy NBA (STBL)")
    parser.add_argument("--season", help="ex. 2026-27 (sinon valeur de settings.json)")
    parser.add_argument("--stage", choices=["draft", "ros"], help="draft ou ros")
    parser.add_argument("--sources", nargs="+", choices=sorted(SOURCES), help="sources à importer")
    parser.add_argument(
        "--file", action="append", default=[], metavar="SOURCE=CHEMIN",
        help="fichier à utiliser pour une source CSV (sinon le plus récent du dossier d'import)",
    )
    parser.add_argument("--skip-link", action="store_true", help="ne pas lancer le rapprochement des joueurs")
    parser.add_argument("-v", "--verbose", action="store_true", help="logs détaillés")
    return parser.parse_args()


def run_pipeline(args):
    settings = load_settings()
    if args.season:
        settings["active_season"] = args.season
    if args.stage:
        settings["active_stage"] = args.stage

    selected = args.sources or [name for name, enabled in settings["sources"].items() if enabled]
    unknown = [name for name in selected if name not in SOURCES]
    if unknown:
        log.warning("Sources inconnues ignorées : %s", ", ".join(unknown))

    log.info("=== Pipeline STBL : saison %s, étape %s ===", settings["active_season"], settings["active_stage"])
    init_db()

    files = {}
    for item in args.file:
        source_name, sep, path = item.partition("=")
        if not sep or source_name not in SOURCES or not issubclass(SOURCES[source_name], CsvProjectionSource):
            log.error("Option --file invalide : '%s' (attendu : source_csv=chemin)", item)
            return 1
        files[source_name] = path

    http = HttpClient.from_settings(settings)
    results = {}
    for name in selected:
        if name not in SOURCES:
            continue
        source_cls = SOURCES[name]
        if issubclass(source_cls, CsvProjectionSource):
            source = source_cls(settings, http=http, file_path=files.get(name))
        else:
            source = source_cls(settings, http=http)
        results[name] = source.run()

    if not args.skip_link:
        link_players(season=settings["active_season"], stage=settings["active_stage"])

    failed = [name for name, ok in results.items() if not ok]
    if failed:
        log.error("Pipeline terminé avec des erreurs : %s", ", ".join(failed))
        return 1
    log.info("Pipeline terminé.")
    return 0


if __name__ == "__main__":
    cli_args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if cli_args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    sys.exit(run_pipeline(cli_args))
