"""Assistant de draft (Rotisserie, snake).

Commandes :
    python -m scripts.draft reco                  # une recommandation (choix lus dans Google Sheets)
    python -m scripts.draft watch                 # veille : recalcule à chaque nouveau choix saisi
    python -m scripts.draft push-projections      # écrit l'onglet "export" (format draft_bdd)

Hors ligne (tests, mock draft) :
    python -m scripts.draft reco --xlsx draft2627.xlsx --until 40
    python -m scripts.draft reco --csv picks.csv  # colonnes round;pick;player

Options communes : --season, --phase, --sims N, --no-sheet (n'écrit pas dans le classeur).
Les paramètres (ordre de draft, keepers, mon équipe, classeur...) sont dans config/league.json.
"""

import argparse
import csv
import hashlib
import json
import logging
import os
import sys
import time

from scripts.config import EXPORTS_DIR, load_league, load_settings
from scripts.draft import report
from scripts.draft.engine import Simulator
from scripts.draft.pool import load_pool
from scripts.draft.projections_tab import build_rows as projection_rows
from scripts.draft.state import build_state, picks_from_csv, picks_from_sheet_rows, picks_from_xlsx
from scripts.sheets import SheetsError, open_spreadsheet, read_range, write_tab

log = logging.getLogger("stbl.draft")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Assistant de draft STBL (roto)")
    parser.add_argument("command", choices=["reco", "watch", "push-projections"])
    parser.add_argument("--season")
    parser.add_argument("--phase", help="phase des projections finales (défaut : active_stage)")
    parser.add_argument("--xlsx", help="lire les choix dans un export Excel (onglet draft_res)")
    parser.add_argument("--csv", help="lire les choix dans un CSV round;pick;player")
    parser.add_argument("--until", type=int, help="ne garder que les N premiers choix (mock draft)")
    parser.add_argument("--sims", type=int, help="nombre de simulations (défaut : league.json)")
    parser.add_argument("--no-sheet", action="store_true", help="ne rien écrire dans Google Sheets")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


class Session:
    def __init__(self, args):
        self.args = args
        self.settings = load_settings()
        self.league = load_league()
        if args.sims:
            self.league["draft"]["simulations"] = args.sims
        self.gs = self.league.get("google_sheets", {})
        self.pool = load_pool(args.season, args.phase)
        self._book = None

    @property
    def offline(self):
        return bool(self.args.xlsx or self.args.csv)

    def book(self):
        if self._book is None:
            self._book = open_spreadsheet(self.gs.get("draft_spreadsheet_id"), self.settings)
        return self._book

    def read_picks(self):
        if self.args.xlsx:
            rows = picks_from_xlsx(self.args.xlsx, self.gs.get("picks_tab", "draft_res"))
        elif self.args.csv:
            rows = picks_from_csv(self.args.csv)
        else:
            raw = read_range(self.book(), self.gs.get("picks_tab", "draft_res"), self.gs.get("picks_range", "A2:D"))
            rows = picks_from_sheet_rows(raw)
        if self.args.until is not None:
            teams = int(self.league["teams"])

            def overall(r):
                try:
                    return (int(float(r[0])) - 1) * teams + int(float(r[1])) - 1
                except (TypeError, ValueError):
                    return 10 ** 9
            rows = [r for r in rows if overall(r) < self.args.until]
        return rows

    def run_once(self, rows):
        state = build_state(self.league, self.pool, rows)
        started = time.time()
        reco = Simulator(self.league, self.pool, state).recommend()
        log.info("Recommandation calculée en %.1f s.", time.time() - started)
        sheet_rows = report.build_rows(reco, state, self.league)
        self._write_csv(sheet_rows)
        if not self.args.no_sheet and not self.offline:
            write_tab(self.book(), self.gs.get("reco_tab", "reco"), sheet_rows)
        print(report.console_summary(reco, state))
        return reco

    def _write_csv(self, rows):
        os.makedirs(EXPORTS_DIR, exist_ok=True)
        path = os.path.join(EXPORTS_DIR, f"reco_{self.settings['active_season']}.csv")
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh, delimiter=self.settings.get("export", {}).get("delimiter", ";"))
            decimal = self.settings.get("export", {}).get("decimal", ",")
            for r in rows:
                writer.writerow([str(v).replace(".", decimal) if isinstance(v, float) else v for v in r])
        log.info("Recommandation écrite dans %s", path)


def watch(session):
    """Relit les choix toutes les poll_seconds secondes et recalcule dès qu'ils changent."""
    poll = max(3, int(session.gs.get("poll_seconds", 10)))
    last = None
    print(f"Veille active (toutes les {poll} s). Ctrl+C pour arrêter.")
    while True:
        try:
            rows = session.read_picks()
            digest = hashlib.sha1(json.dumps(rows, default=str).encode()).hexdigest()
            if digest != last:
                session.run_once(rows)
                last = digest
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # une erreur réseau ne doit pas arrêter la veille
            log.error("Erreur pendant la veille : %s", exc)
        time.sleep(poll)


def push_projections(session):
    rows = projection_rows(session.args.season, session.args.phase)
    book = open_spreadsheet(session.gs.get("projections_spreadsheet_id") or session.gs.get("draft_spreadsheet_id"),
                            session.settings)
    write_tab(book, session.gs.get("projections_tab", "export"), rows)
    print(f"{len(rows) - 1} joueurs écrits dans l'onglet '{session.gs.get('projections_tab', 'export')}'.")


def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    try:
        session = Session(args)
        if args.command == "push-projections":
            push_projections(session)
        elif args.command == "watch":
            watch(session)
        else:
            session.run_once(session.read_picks())
    except KeyboardInterrupt:
        print("\nVeille arrêtée.")
    except (SheetsError, ValueError, RuntimeError) as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
