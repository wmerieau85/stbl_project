"""Pipeline STBL : import des projections, rapprochement des joueurs, puis pondération.

Exemples :
    python main.py                        # sources et étape définies dans l'onglet settings
    python main.py --stage ros            # projections "rest of season" + stats par période (lt, st)
    python main.py --sources cbs          # une seule source
    python main.py --file fanscout=C:/chemin/table.csv   # fichier CSV précis
    python main.py --skip-import          # recalcule seulement la pondération
    python main.py --push-sheet           # ... puis envoie les projections (toutes phases) dans l'onglet bdd
    python main.py --skip-yahoo           # sans le pré-classement / ADP Yahoo (étape draft)
"""

import argparse
import logging
import sys

from scripts.config import enabled_sources, load_bootstrap, load_settings
from scripts.db import init_db
from scripts.exports import export_raw_projections
from scripts.http_client import HttpClient
from scripts.player_linker import link_players
from scripts.sources import SOURCES
from scripts.sources.csv_source import CsvProjectionSource
from scripts.weighting import run_phases

log = logging.getLogger("stbl")


def parse_args():
    parser = argparse.ArgumentParser(description="Pipeline de projections fantasy NBA (STBL)")
    parser.add_argument("--season", help="ex. 2026-27 (sinon valeur de l'onglet settings)")
    parser.add_argument("--stage", choices=["draft", "ros"], help="draft ou ros")
    parser.add_argument("--sources", nargs="+", choices=sorted(SOURCES), help="sources à importer")
    parser.add_argument(
        "--file", action="append", default=[], metavar="SOURCE=CHEMIN",
        help="fichier à utiliser pour une source CSV (sinon le plus récent du dossier d'import)",
    )
    parser.add_argument("--skip-import", action="store_true", help="ne pas réimporter les sources")
    parser.add_argument("--skip-link", action="store_true", help="ne pas lancer le rapprochement des joueurs")
    parser.add_argument("--skip-yahoo", action="store_true",
                        help="ne pas récupérer le pré-classement et l'ADP Yahoo (étape draft)")
    parser.add_argument("--skip-weighting", action="store_true", help="ne pas calculer les projections finales")
    parser.add_argument("--push-sheet", action="store_true",
                        help="écrire les projections finales (toutes phases) dans Google Sheets (onglet bdd)")
    parser.add_argument("--no-sync-config", action="store_true",
                        help="ne pas relire l'onglet settings du classeur (fichiers config/ utilisés tels quels)")
    parser.add_argument("-v", "--verbose", action="store_true", help="logs détaillés")
    return parser.parse_args()


def sync_config():
    """Relit l'onglet settings (paramètres, sources, grilles, alias) avant le calcul, si le classeur est accessible."""
    boot = load_bootstrap()
    if not (boot.get("spreadsheet_id") and boot.get("config_tab")):
        return
    try:
        from scripts import config_sheet
        from scripts.sheets import open_spreadsheet

        sheets_settings = {"google": boot["google"]}
        config_sheet.pull(open_spreadsheet(boot["spreadsheet_id"], sheets_settings), boot["config_tab"])
    except ValueError as exc:  # contenu de l'onglet incohérent : on s'arrête, pour ne pas calculer à tort
        raise ValueError(f"Onglet config : {exc}") from None
    except Exception as exc:  # classeur inaccessible : fichiers locaux
        log.warning("Onglet config non relu (%s) : fichiers de config/ utilisés tels quels.", exc)


def update_yahoo_rankings(settings):
    """Pré-classement et ADP Yahoo de ma ligue (colonnes Yahoo / ADP Yahoo de bdd). Non bloquant."""
    from scripts.config import load_league
    from scripts.yahoo import rankings
    from scripts.yahoo.client import YahooError

    league = load_league()
    if not str((league.get("yahoo") or {}).get("league_id") or "").strip():
        log.info("[Yahoo] Pas d'ID de ligue : pré-classement Yahoo non récupéré.")
        return
    try:
        rankings.update(settings, league)
    except (YahooError, OSError, ValueError, KeyError) as exc:
        log.warning("[Yahoo] Pré-classement non mis à jour : %s", exc)


def run_pipeline(args):
    try:
        if not args.no_sync_config:
            sync_config()
        settings = load_settings()
    except ValueError as exc:
        log.error("Configuration invalide : %s", exc)
        return 2
    if args.season:
        settings["active_season"] = args.season
    if args.stage:
        settings["active_stage"] = args.stage

    selected = args.sources or enabled_sources(settings)
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
    for name in ([] if args.skip_import else selected):
        if name not in SOURCES:
            continue
        source_cls = SOURCES[name]
        if issubclass(source_cls, CsvProjectionSource):
            source = source_cls(settings, http=http, file_path=files.get(name))
        else:
            source = source_cls(settings, http=http)
        results[name] = source.run()
        if settings["active_stage"] == "ros":
            for window in settings.get("stats_windows", {}).get(name, []):
                window_settings = dict(settings, active_stage=window)
                if not source_cls(window_settings, http=http).run():
                    # stats par période : absentes hors saison ou en tout début de saison, non bloquant
                    log.warning("[%s] Stats '%s' non mises à jour (données précédentes conservées).", name, window)

    if not args.skip_link:
        link_players(season=settings["active_season"], stage=settings["active_stage"])

    if settings["active_stage"] == "draft" and not args.skip_yahoo and not args.skip_import:
        update_yahoo_rankings(settings)

    export_raw_projections(settings=settings)

    if not args.skip_weighting and not run_phases(settings):
        results["pondération"] = False

    if args.push_sheet and results.get("pondération", True):
        from scripts.draft.__main__ import push_projections
        from scripts.sheets import SheetsError
        try:
            push_projections(season=settings["active_season"])
        except (SheetsError, OSError, ValueError) as exc:
            log.error("Envoi vers Google Sheets impossible : %s", exc)
            results["google sheets"] = False

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
