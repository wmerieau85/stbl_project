"""Lanceur des commandes du menu du classeur (traitement GitHub Actions, ou PC).

    python -m scripts.run season_update --key 20261017-101500-season_update
    python -m scripts.run --list

Seuls les codes de COMMANDS sont acceptés (jamais une ligne de commande libre). Pour chaque lancement :
onglet settings relu, base Drive (et CSV des sources) téléchargés, commande(s) lancée(s), base renvoyée
si elle a changé, ligne du journal (onglet runs) mise à jour : en cours, puis OK / erreur, durée, lien
vers le log GitHub et dernières lignes en cas d'erreur.
"""

import argparse
import collections
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime

from scripts.config import BASE_DIR, load_bootstrap

log = logging.getLogger("stbl.run")


@dataclass(frozen=True)
class Command:
    label: str
    steps: tuple          # arguments de python, une étape par tuple
    db: bool = True       # base nécessaire
    write: bool = False   # base modifiée : renvoyée sur Drive
    imports: bool = False  # CSV des sources à copier depuis Drive
    history: bool = False  # history.sqlite téléchargé, puis renvoyé s'il a changé


def _m(*args):
    return ("-m",) + args


COMMANDS = {
    "projections_update": Command("Projections > Mise à jour", (("main.py", "--push-sheet"),), write=True, imports=True),
    "projections_recalc": Command("Projections > Recalcul seul", (("main.py", "--skip-import", "--push-sheet"),),
                                  write=True),
    "lottery_draw": Command("Draft > Tirage lottery", (_m("scripts.lottery", "--draw"),), db=False),
    "draft_reco": Command("Draft > Reco", (_m("scripts.draft", "reco"),)),
    "draft_watch": Command("Draft > Watch", (_m("scripts.draft", "watch"),)),
    "yahoo_draft": Command("Draft > Yahoo Draft", (_m("scripts.yahoo", "draft"),)),
    "yahoo_rankings": Command("Draft > Yahoo Rankings", (_m("scripts.yahoo", "rankings"),), write=True),
    "yahoo_rosters": Command("Yahoo > MàJ rosters", (_m("scripts.yahoo", "rosters"),)),
    "yahoo_stats": Command("Yahoo > Standings + Team Log", (_m("scripts.yahoo", "stats"),)),
    "season_update": Command("Saison > Projections", (_m("scripts.season", "update"),)),
    "season_moves": Command("Saison > Moves", (_m("scripts.season", "moves"),)),
    "season_all": Command("Saison > Tout mettre à jour", (_m("scripts.yahoo", "rosters"), _m("scripts.yahoo", "stats"),
                                                          _m("scripts.season", "update"),
                                                          _m("scripts.season", "moves"))),
    "history_snapshot": Command("Maintenance > Photo des sources", (_m("scripts.history", "daily"),),
                                imports=True, history=True),
    "sources_accuracy": Command("Maintenance > Pertinence des sources", (_m("scripts.accuracy"),), history=True),
    "settings_check": Command("Maintenance > Contrôle settings", (_m("scripts.config_sheet", "check"),), db=False),
}

RUNS_HEADER = ["Clé", "Demandé le", "Menu", "Statut", "Début", "Fin", "Durée (s)", "Log", "Détail"]
TAIL = 12


def now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Paris")).replace(tzinfo=None)
    except Exception:   # pragma: no cover - zoneinfo absent
        return datetime.now()


def stamp(moment):
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def run_url():
    server, repo, run_id = (os.environ.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{server}/{repo}/actions/runs/{run_id}" if server and repo and run_id else ""


class Journal:
    """Ligne du lancement dans l'onglet runs (créée si le lancement ne vient pas du menu)."""

    def __init__(self, book, tab, key, label):
        self.ws, self.key, self.label, self.row = None, key, label, None
        if book is None:
            return
        import gspread

        try:
            try:
                self.ws = book.worksheet(tab)
            except gspread.WorksheetNotFound:
                self.ws = book.add_worksheet(title=tab, rows=200, cols=len(RUNS_HEADER))
                self.ws.update(values=[RUNS_HEADER], range_name="A1")
            keys = self.ws.col_values(1)
            if not keys:
                self.ws.update(values=[RUNS_HEADER], range_name="A1")
                keys = [RUNS_HEADER[0]]
            if key in keys:
                self.row = keys.index(key) + 1
            else:
                self.row = len(keys) + 1
                self.ws.update(values=[[key, stamp(now()), label]], range_name=f"A{self.row}")
        except Exception as exc:   # le journal ne doit jamais empêcher la commande
            log.warning("Journal %s inaccessible (%s).", tab, exc)
            self.ws = None

    def update(self, status, start=None, end=None, detail=""):
        if self.ws is None:
            return
        duration = round((end - start).total_seconds()) if start and end else ""
        values = [[self.label, status, stamp(start) if start else "", stamp(end) if end else "", duration,
                   run_url(), detail[:45000]]]
        try:
            self.ws.update(values=values, range_name=f"C{self.row}:I{self.row}")
        except Exception as exc:
            log.warning("Journal non mis à jour (%s).", exc)


def run_step(args, env):
    """Lance une étape, affiche sa sortie au fil de l'eau ; renvoie (code retour, dernières lignes)."""
    tail = collections.deque(maxlen=TAIL)
    proc = subprocess.Popen([sys.executable, *args], cwd=BASE_DIR, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1)
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        if line.strip():
            tail.append(line.rstrip())
    return proc.wait(), list(tail)


def open_book():
    """(classeur, configuration de la ligue) : onglet settings relu, comme au début de chaque commande."""
    from scripts import config_sheet
    from scripts.config import load_league
    from scripts.sheets import open_spreadsheet

    boot = load_bootstrap()
    book = open_spreadsheet(boot["spreadsheet_id"], {"google": boot.get("google", {})})
    try:
        league = config_sheet.pull(book, boot.get("config_tab") or "settings")
    except Exception as exc:
        log.warning("Onglet settings non relu (%s) : configuration locale.", exc)
        league = load_league()
    return book, league


def execute(code, key=None, book=None, league=None, step_runner=run_step, drive_factory=None):
    from scripts import cloud

    command = COMMANDS[code]
    key = key or f"{now():%Y%m%d-%H%M%S}-{code}"
    tab = ((league or {}).get("google_sheets") or {}).get("runs_tab") or "runs"
    journal = Journal(book, tab, key, command.label)
    start = now()
    journal.update("en cours", start)
    env = dict(os.environ, **{cloud.RUNNER_ENV: "1", "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"})
    detail, rc, drive, before, before_history = [], 0, None, None, None
    t0 = time.monotonic()
    try:
        if command.db:
            drive = (drive_factory or cloud.Drive)(cloud.folder_id(league))
            before = cloud.pull_db(drive)
            if command.imports:
                cloud.pull_imports(drive)
            if command.history:
                before_history = cloud.pull_history(drive)
        for args in command.steps:
            log.info("=== %s", " ".join(args))
            rc, tail = step_runner(args, env)
            if rc != 0:
                detail = [f"échec de : python {' '.join(args)} (code {rc})"] + tail
                break
        if rc == 0 and command.write and drive is not None:
            cloud.push_db(drive, before)
        if rc == 0 and command.history and drive is not None:
            cloud.push_history(drive, before_history)
    except Exception as exc:   # Drive, classeur... : message lisible dans le journal
        log.error("%s", exc)
        rc, detail = rc or 1, detail + [str(exc)]
    end = now()
    journal.update("OK" if rc == 0 else "erreur", start, end, "\n".join(detail))
    log.info("%s : %s en %.0f s.", command.label, "OK" if rc == 0 else "erreur", time.monotonic() - t0)
    return rc


def main(argv=None):
    parser = argparse.ArgumentParser(description="Lanceur des commandes du menu STBL")
    parser.add_argument("command", nargs="?", choices=sorted(COMMANDS))
    parser.add_argument("--key", help="clé de la ligne du journal (onglet runs), créée par le menu")
    parser.add_argument("--list", action="store_true", help="affiche les commandes disponibles")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    if args.list or not args.command:
        for code, c in COMMANDS.items():
            print(f"{code:<20} {c.label:<34} " + " && ".join("python " + " ".join(s) for s in c.steps))
        return 0
    try:
        book, league = open_book()
    except Exception as exc:
        log.error("Classeur inaccessible : %s", exc)
        return 1
    return execute(args.command, args.key, book, league)


if __name__ == "__main__":
    sys.exit(main())
