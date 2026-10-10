"""Connexion à l'API Yahoo Fantasy.

    python -m scripts.yahoo auth      # autorisation (une seule fois)
    python -m scripts.yahoo leagues   # mes ligues NBA de la saison, avec leur ID
    python -m scripts.yahoo check     # réglages, équipes et ordre de draft de la ligue configurée
    python -m scripts.yahoo draft     # choix de draft effectués (+ exports/yahoo_draft_<saison>.csv)
    python -m scripts.yahoo rankings  # pré-classement (O-Rank) et ADP Yahoo -> base (+ exports/yahoo_rankings_<saison>.csv)
"""

import argparse
import csv
import logging
import os
import sys

import requests

from scripts.config import EXPORTS_DIR, load_league, load_settings
from scripts.sheets import SheetsError, open_spreadsheet
from scripts.yahoo.client import YahooClient, YahooError
from scripts.yahoo.league import draft_results, league_key, league_settings, my_leagues, teams
from scripts.yahoo import public, sheets as yahoo_sheets


def _sync_config(settings, league):
    """Relit l'onglet settings du classeur (s'il est accessible) pour récupérer l'ID de ligue à jour."""
    gs = league.get("google_sheets", {})
    if not (gs.get("draft_spreadsheet_id") and gs.get("config_tab")):
        return league
    try:
        from scripts import config_sheet
        from scripts.sheets import SheetsError, open_spreadsheet

        config_sheet.pull(open_spreadsheet(gs["draft_spreadsheet_id"], settings), gs["config_tab"])
        return load_league()
    except Exception as exc:  # classeur inaccessible : on garde la configuration locale
        logging.warning("Onglet config non relu (%s) : utilisation de la configuration locale.", exc)
        return league


def _key(client, league):
    league_id = str(league.get("yahoo", {}).get("league_id") or "").strip()
    if not league_id:
        raise YahooError("ID de ligue Yahoo absent. Lancez python -m scripts.yahoo leagues pour le connaître, puis "
                         "renseignez « ID de la ligue Yahoo » dans l'onglet settings (ou utilisez --league <ID>).")
    return league_key(client, league_id)


def main(argv=None):
    parser = argparse.ArgumentParser(description="API Yahoo Fantasy")
    parser.add_argument("command", choices=["auth", "leagues", "check", "draft", "rankings", "stats"])
    parser.add_argument("--count", type=int, help="rankings : nombre de joueurs (défaut 300)")
    parser.add_argument("--no-browser", action="store_true", help="ne pas ouvrir le navigateur (auth)")
    parser.add_argument("--league", help="ID de la ligue Yahoo (sinon onglet settings)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    settings, league = load_settings(), load_league()
    client = YahooClient(settings)
    if args.command in ("check", "draft", "rankings", "stats"):
        league = _sync_config(settings, league) if not args.league else league
        if args.league:
            league.setdefault("yahoo", {})["league_id"] = args.league
    try:
        if args.command == "auth":
            client.authorize(open_browser=not args.no_browser)
            args.command = "leagues"
        if args.command == "leagues":
            found = my_leagues(client)
            if not found:
                print("Aucune ligue NBA trouvée pour ce compte cette saison.")
            for lg in found:
                print(f"ID {lg['league_id']:>8} | {lg['name']} | saison {lg['season']} | {lg['num_teams']} équipes"
                      f" | {lg['scoring_type']} | draft : {lg['draft_status']}")
        elif args.command == "check":
            key = _key(client, league)
            s = league_settings(client, key)
            print(f"Ligue {s['name']} ({key}), saison {s['season']}, {s['num_teams']} équipes, {s['scoring_type']}")
            print(f"Draft : {s['draft_type']} ({s['draft_status']}), keepers : {s['uses_keepers']}")
            print("Roster : " + ", ".join(f"{p} {n}" for p, n in s["roster"].items()))
            print("Catégories : " + ", ".join(s["categories"]))
            if s.get("max_games_played"):
                print(f"Matchs max : {s['max_games_played']}")
            names = {t["team_key"]: t for t in teams(client, key)}
            print("\nÉquipes :")
            for t in names.values():
                print(f"  {t['name']} (manager : {t['manager']})")
            picks = draft_results(client, key)
            print(f"\nChoix de draft déjà effectués : {sum(1 for p in picks if p['player'])} / {len(picks)}")
        elif args.command == "draft":
            key = _key(client, league)
            names = {t["team_key"]: t for t in teams(client, key)}
            picks = [p for p in draft_results(client, key) if p["player"]]
            os.makedirs(EXPORTS_DIR, exist_ok=True)
            path = os.path.join(EXPORTS_DIR, f"yahoo_draft_{settings['active_season']}.csv")
            with open(path, "w", encoding="utf-8-sig", newline="") as fh:
                w = csv.writer(fh, delimiter=";")
                w.writerow(["pick", "round", "team", "manager", "player", "positions", "nba_team"])
                for p in picks:
                    t = names.get(p["team_key"], {})
                    w.writerow([p["pick"], p["round"], t.get("name", p["team_key"]), t.get("manager", ""),
                                p["player"], p["positions"], p["nba_team"]])
                    print(f"{p['pick']:>3} (tour {p['round']:>2}) {t.get('name', p['team_key']):<28} {p['player']}")
            print(f"{len(picks)} choix écrits dans {path}")
        elif args.command == "rankings":
            from scripts.db import init_db
            from scripts.yahoo import rankings

            init_db()
            rows = rankings.update(settings, league, args.count)
            os.makedirs(EXPORTS_DIR, exist_ok=True)
            path = os.path.join(EXPORTS_DIR, f"yahoo_rankings_{settings['active_season']}.csv")
            with open(path, "w", encoding="utf-8-sig", newline="") as fh:
                w = csv.writer(fh, delimiter=";")
                w.writerow(["rank", "player", "nba_team", "positions", "adp", "avg_round", "pct_drafted", "player_id"])
                for r in rows:
                    w.writerow([r["rank"], r["player"], r["nba_team"], r["positions"],
                                "" if r["adp"] is None else str(r["adp"]).replace(".", ","),
                                "" if r["avg_round"] is None else str(r["avg_round"]).replace(".", ","),
                                "" if r["pct_drafted"] is None else str(r["pct_drafted"]).replace(".", ","),
                                r.get("player_id") or ""])
            for r in rows[:15]:
                print(f"{r['rank']:>3}. {r['player']:<28} {r['positions']:<10} ADP {r['adp'] if r['adp'] is not None else '-'}")
            print(f"{len(rows)} joueurs écrits dans {path}")
        elif args.command == "stats":
            league_id = str(league.get("yahoo", {}).get("league_id") or "").strip()
            if not league_id:
                raise YahooError("ID de ligue Yahoo absent. Renseignez « ID de la ligue Yahoo » dans l'onglet settings.")
            gs = league.get("google_sheets", {})
            book = open_spreadsheet(gs.get("draft_spreadsheet_id"), settings)
            tab = gs.get("yahoo_tab", "yahoo")
            timeout = settings.get("http", {}).get("timeout", 30)
            season = settings["active_season"]
            team_names = public.teams(league_id, timeout, season=season)
            if not team_names:
                raise public.PublicPageError("Aucune équipe trouvée dans le classement public Yahoo.")

            manager_by_team = {
                team_name.strip().casefold(): manager
                for manager, team_name in (league.get("yahoo", {}).get("teams") or {}).items()
                if isinstance(team_name, str)
            }
            team_rows, without_log = [], []
            for team in team_names:
                name = team["name"]
                manager = manager_by_team.get(name.strip().casefold(), "")
                if not manager:
                    logging.warning("Équipe Yahoo non reliée à un manager : %s", name)
                try:
                    players = public.team_log(league_id, team["team_id"], season, timeout)
                except public.TeamLogNotPublished:
                    players = []
                    without_log.append(name)
                team_rows.append({"team_id": team["team_id"], "team": name, "manager": manager, "players": players})
            standings = public.standings(league_id, timeout, season=season)
            yahoo_sheets.write_standings(book, tab, team_rows, standings)
            yahoo_sheets.write_team_log(book, tab, team_rows)
            if len(without_log) == len(team_rows):
                logging.warning("Team Log pas encore publié par Yahoo (après la première semaine de jeu) : "
                                "bloc Team Log vide, standings écrits.")
            elif without_log:
                logging.warning("Team Log vide pour %d équipe(s) : %s", len(without_log), ", ".join(without_log))
            print(f"Standings de {len(team_rows)} équipes et Team Log de {len(team_rows) - len(without_log)} "
                  f"équipe(s) écrits dans l'onglet {tab}.")
    except YahooError as exc:
        logging.error("%s", exc)
        return 1
    except (public.PublicPageError, requests.RequestException, SheetsError, OSError, ValueError) as exc:
        logging.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
