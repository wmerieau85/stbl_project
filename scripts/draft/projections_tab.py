"""Projections finales au format de l'onglet « export » lu par draft_bdd (IMPORTRANGE A1:BH).

Colonnes : A-R stats par match, T-AD z-scores AVG + EFF + RANG, AF-AT totaux,
AV-BF z-scores TOT + EFF + RANG, BH rang Yahoo (ordre ADP, puis rang TOT sans ADP).
EFF = somme pondérée des z-scores / somme des poids (moyenne des 9 catégories).
"""

from scripts.config import load_league, load_settings
from scripts.db import get_connection

Z_CATS = ("fgp", "fg3m", "ftp", "reb", "ast", "stl", "blk", "tov", "pts")
HEADER = (
    ["Player", "Pos", "Tm", "G", "MIN", "FG ", "FGA ", "FG%", "3P ", "FT ", "FTA ", "FT%",
     "TRB ", "AST ", "STL ", "BLK ", "TO", "PTS", ""]
    + ["FG%", "3P", "FT%", "TRB", "AST", "STL", "BLK", "TO", "PTS", "EFF", "RANG", ""]
    + ["G", "MIN", "FG", "FGA", "FG%", "3P", "FT", "FTA", "FT%", "TRB", "AST", "STL", "BLK", "TO", "PTS", ""]
    + ["FG%", "3P", "FT%", "TRB", "AST", "STL", "BLK", "TO", "PTS", "EFF", "RANG", ""]
    + ["Yahoo"]
)


def build_rows(season=None, phase=None):
    settings = load_settings()
    season = season or settings["active_season"]
    phase = phase or settings["active_stage"]
    league = load_league()
    weight_sum = sum(float(league["categories"].get(c, 0) or 0) for c in Z_CATS) or 1.0
    with get_connection() as conn:
        cursor = conn.execute("SELECT * FROM final_projections WHERE phase=? AND season=? ORDER BY rank_tot",
                              (phase, season))
        names = [d[0] for d in cursor.description]
        players = [dict(zip(names, r)) for r in cursor.fetchall()]

    with_adp = sorted((p for p in players if p["adp"] is not None), key=lambda p: p["adp"])
    without = [p for p in players if p["adp"] is None]  # déjà triés par rang TOT
    yahoo = {p["player_id"]: i for i, p in enumerate(with_adp + without, 1)}

    rows = [HEADER]
    for p in players:
        gp = p["gp"] or 0

        def pg(field):
            return (p[field] or 0) / gp if gp else 0

        def tot(field):
            return round(p[field] or 0)

        per_game = [gp and round(gp), p["mpg"], pg("fgm"), pg("fga"), p["fgp"], pg("fg3m"), pg("ftm"),
                    pg("fta"), p["ftp"], pg("reb"), pg("ast"), pg("stl"), pg("blk"), pg("tov"), pg("pts")]
        z_avg = [p[f"z_{c}_avg"] for c in Z_CATS] + [(p["z_sum_avg"] or 0) / weight_sum, p["rank_avg"]]
        totals = [round(gp), round(p["min"] or 0), tot("fgm"), tot("fga"), p["fgp"], tot("fg3m"), tot("ftm"),
                  tot("fta"), p["ftp"], tot("reb"), tot("ast"), tot("stl"), tot("blk"), tot("tov"), tot("pts")]
        z_tot = [p[f"z_{c}_tot"] for c in Z_CATS] + [(p["z_sum_tot"] or 0) / weight_sum, p["rank_tot"]]
        rows.append([p["player"], p["positions"], p["team"]] + per_game + [""] + z_avg + [""]
                    + totals + [""] + z_tot + [""] + [yahoo[p["player_id"]]])
    return rows
