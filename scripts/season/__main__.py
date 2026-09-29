"""Module saison : classement réel et projeté en fin de saison.

    python -m scripts.season update             # lit Yahoo, projette, écrit l'onglet season (détail des effectifs en CSV)
    python -m scripts.season update --no-sheet  # console + CSV seulement
    python -m scripts.season update --rosters sheet   # effectifs de l'onglet rosters (simulation de draft)

Données : pages publiques de la ligue Yahoo (effectifs, compteur de matchs par poste, classement),
calendrier NBA, projections lt (reste de la saison) et st (forme récente, 15 prochains jours).
"""

import argparse
import csv
import logging
import os
import sys
from datetime import date, datetime

from scripts.config import EXPORTS_DIR, load_league, load_settings
from scripts.draft.pool import load_pool
from scripts.season import report
from scripts.season.engine import CATEGORY_ORDER, project_team, standings_projection, gp_inconsistencies
from scripts.season.schedule import load_games
from scripts.yahoo import public

log = logging.getLogger("stbl.season")


def _pool(season, phase, fallback=None):
    try:
        pool = load_pool(season=season, phase=phase)
        pool.phase = phase
        return pool
    except RuntimeError:
        if fallback:
            log.warning("Pas de projections %s : utilisation de %s.", phase, fallback)
            pool = load_pool(season=season, phase=fallback)
            pool.phase = fallback
            return pool
        log.warning("Pas de projections %s.", phase)
        return None


PLAYER_HEADERS = ("player", "joueur", "players")
MANAGER_HEADERS = ("team", "manager", "équipe", "equipe")


def rosters_from_sheet(book, tab, league):
    """Effectifs lus dans un onglet du classeur (ex. rosters de la simulation de draft).

    Colonnes repérées par leur en-tête (1re ligne) : joueur (Player / Joueur) et manager
    (Team / Manager). Les lignes sans joueur sont ignorées.
    """
    from scripts.sheets import read_range

    rows = read_range(book, tab, "A1:ZZ")
    if not rows:
        raise ValueError(f"Onglet {tab} vide ou introuvable.")
    header = [str(c).strip().lower() for c in rows[0]]
    try:
        col_player = next(i for i, h in enumerate(header) if h in PLAYER_HEADERS)
        col_team = next(i for i, h in enumerate(header) if h in MANAGER_HEADERS)
    except StopIteration:
        raise ValueError(f"Onglet {tab} : colonnes « Player » et « Team » (ou « Joueur » et « Manager ») "
                         "introuvables en 1re ligne.") from None
    managers = league["draft"].get("order") or []
    rosters = {m: {"players": [], "games": {}} for m in managers}
    ignored = set()
    for r in rows[1:]:
        r = list(r) + [""] * (max(col_player, col_team) + 1 - len(r))
        player, manager = str(r[col_player] or "").strip(), str(r[col_team] or "").strip()
        if not player or not manager:
            continue
        if manager not in rosters:
            ignored.add(manager)
            continue
        rosters[manager]["players"].append({"player": player, "player_id": "", "nba_team": "", "positions": "",
                                            "slot": "", "status": ""})
    if ignored:
        log.warning("Onglet %s : managers inconnus de la config ignorés : %s", tab, ", ".join(sorted(ignored)))
    log.info("[Saison] Effectifs lus dans l'onglet %s : %d joueurs.", tab,
             sum(len(v["players"]) for v in rosters.values()))
    return rosters


def compute(settings, league, today=None, book=None, roster_source=None):
    season = settings["active_season"]
    conf = league.get("season", {})
    league_id = str(league.get("yahoo", {}).get("league_id") or "").strip()
    source = (roster_source or conf.get("roster_source", "yahoo")).lower()
    if source == "yahoo" and not league_id:
        raise ValueError("ID de ligue Yahoo absent (onglet config > ID de la ligue Yahoo).")
    timeout = settings.get("http", {}).get("timeout", 30)

    lt_pool = _pool(season, conf.get("lt_phase", "lt"), fallback="draft")
    st_pool = _pool(season, conf.get("st_phase", "st"))
    games = load_games(season, timeout=timeout)
    start = min(g[0] for g in games)
    today = today or date.today()
    if today.isoformat() < start:
        today = date.fromisoformat(start)

    team_names = league.get("yahoo", {}).get("teams") or {}
    names = {v: k for k, v in team_names.items()}
    standings = {}
    if league_id:
        try:
            standings = public.standings(league_id, timeout)
        except (public.PublicPageError, OSError) as exc:
            if source == "yahoo":
                raise
            log.warning("Classement Yahoo indisponible (%s) : projection sans stats réelles.", exc)
    teams = []
    horizon = int(conf.get("st_days", 15))
    if source == "sheet":
        if book is None:
            raise ValueError("Effectifs de l'onglet du classeur demandés, mais Google Sheets n'est pas utilisé "
                             "(option --no-sheet ?).")
        tab = conf.get("roster_tab", "rosters")
        for manager, roster in rosters_from_sheet(book, tab, league).items():
            name = team_names.get(manager, manager)
            teams.append(project_team(manager, name, roster, standings.get(name), lt_pool, st_pool, games,
                                      today, horizon, league))
    else:
        for t in public.teams(league_id, timeout):
            roster = public.roster(league_id, t["team_id"], timeout)
            manager = names.get(t["name"], t["name"])
            teams.append(project_team(manager, t["name"], roster, standings.get(t["name"]), lt_pool, st_pool,
                                      games, today, horizon, league))
    if getattr(lt_pool, "phase", None) != "draft":   # les projections de draft sont sur la saison entière
        bad = gp_inconsistencies(teams)
        if bad:
            log.warning("%d joueur(s) avec plus de matchs projetés (lt) que de matchs restants au calendrier : "
                        "une source ROS donne peut-être des totaux sur la saison entière. Ex. : %s", len(bad),
                        ", ".join(f"{n} ({g:.0f} pour {s} restants)" for n, g, s in bad[:5]))
    unknown = sorted({p.display or p.name for t in teams for p in t.players if not p.found})
    if unknown:
        log.warning("%d joueur(s) sans projection (ignorés) : %s", len(unknown), ", ".join(unknown[:15]))
    cats = [c for c in CATEGORY_ORDER if float(league["categories"].get(c, 0) or 0) > 0]
    weights = [float(league["categories"][c]) for c in cats]
    proj = standings_projection(teams, cats, weights, sims=int(conf.get("simulations", 2000)))
    return {"teams": teams, "categories": cats, "projection": proj, "today": today,
            "my_team": league["draft"].get("my_team"), "unknown": unknown}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Module saison STBL")
    parser.add_argument("command", choices=["update"])
    parser.add_argument("--no-sheet", action="store_true", help="ne pas écrire dans Google Sheets")
    parser.add_argument("--date", help="date de calcul AAAA-MM-JJ (défaut : aujourd'hui)")
    parser.add_argument("--rosters", choices=["yahoo", "sheet"],
                        help="effectifs : Yahoo ou onglet du classeur (sinon réglage de l'onglet config)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    settings, league = load_settings(), load_league()
    gs = league.get("google_sheets", {})
    book = None
    if not args.no_sheet and gs.get("draft_spreadsheet_id"):
        from scripts import config_sheet
        from scripts.sheets import open_spreadsheet
        book = open_spreadsheet(gs["draft_spreadsheet_id"], settings)
        if gs.get("config_tab"):
            league = config_sheet.pull(book, gs["config_tab"])
    try:
        result = compute(settings, league, date.fromisoformat(args.date) if args.date else None,
                         book=book, roster_source=args.rosters)
    except (ValueError, RuntimeError, public.PublicPageError) as exc:
        log.error("%s", exc)
        return 1
    season_rows = report.season_rows(result, league)
    roster_rows = report.roster_rows(result)
    os.makedirs(EXPORTS_DIR, exist_ok=True)
    for name, rows in (("season", season_rows), ("season_rosters", roster_rows)):
        path = os.path.join(EXPORTS_DIR, f"{name}_{settings['active_season']}.csv")
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            csv.writer(fh, delimiter=";").writerows(rows)
    if book is not None:
        from scripts.sheets import write_tab
        write_tab(book, gs.get("season_tab") or "season", season_rows)
    print(report.console(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
