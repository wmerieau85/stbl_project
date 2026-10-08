"""Onglet bdd_detail : les projections de chaque source, à côté de la projection pondérée.

Une ligne par joueur et par source (et par étape : draft, ros, sea, l30...), plus une ligne
« Pondéré » par phase calculée (draft, lt, st...). Stats par match ; les z-scores et rangs sont
calculés avec le même groupe de référence que la phase pondérée correspondante (draft pour
l'étape draft, lt sinon) : une source plus optimiste sur un joueur lui donne un meilleur rang.

Source en moyennes par match sans nombre de matchs (RotoBaller) : ses moyennes sont ramenées
aux matchs de la projection pondérée (colonne G), comme dans le calcul, pour que ses totaux,
z-scores et rangs soient comparables aux autres sources.

Sans tirs tentés (FantasyPros, DraftKick, Fantasy Nerds...), les tentatives par match de la
projection pondérée sont reprises avec le pourcentage de la source (colonne « Tirs estimés »).
"""

import bisect

from scripts.config import load_league, load_settings
from scripts.db import get_connection
from scripts.weighting.weights import source_codes
from scripts.weighting.zscores import CATEGORIES, apply_params, reference_params

HEADER = ["Player", "Source", "Code", "Stage", "Tm", "Pos", "G", "MIN", "PTS", "TRB", "AST", "STL", "BLK", "TO",
          "3P", "FG", "FGA", "FG%", "FT", "FTA", "FT%", "ADP", "Tirs estimés",
          "EFF AVG", "RANG AVG", "EFF TOT", "RANG TOT", "RANG final"]
FINAL_LABEL = "Pondéré"


def _rows(conn, sql, args):
    cursor = conn.execute(sql, args)
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, r)) for r in cursor.fetchall()]


def _per_game(p, field):
    gp = p.get("gp") or 0
    return round((p.get(field) or 0) / gp, 2) if gp else ""


def _r(value, digits):
    return round(value, digits) if isinstance(value, (int, float)) else ""


def _per_game_labels():
    from scripts.sources import SOURCES

    return {cls.label for cls in SOURCES.values() if cls.per_game}


def _games_from_final(row, final):
    """Moyennes par match sans matchs projetés -> totaux sur les matchs de la projection pondérée."""
    from scripts.sources.base import COUNTING_COLUMNS

    games = final["gp"]
    for col in COUNTING_COLUMNS:
        if row.get(col) is not None:
            row[col] = row[col] * games
    row["gp"] = games


def _estimate_shots(row, final):
    """Tentatives absentes : tentatives par match de la projection pondérée x matchs de la source."""
    estimated = False
    gp = row.get("gp") or 0
    for made, att, pct in (("fgm", "fga", "fgp"), ("ftm", "fta", "ftp")):
        if row.get(att) or row.get(pct) is None or not final or not final.get("gp") or not gp:
            continue
        row[att] = (final.get(att) or 0) / final["gp"] * gp
        row[made] = row[pct] * row[att]
        estimated = True
    return estimated


def build_detail_rows(season=None):
    settings = load_settings()
    league = load_league()
    season = season or settings["active_season"]
    weight_sum = sum(float(league["categories"].get(c, 0) or 0) for c in CATEGORIES) or 1.0
    labels = {label: code for code, label in source_codes().items()}
    with get_connection() as conn:
        finals = _rows(conn, "SELECT * FROM final_projections WHERE season=? ORDER BY phase, rank_tot", (season,))
        raws = _rows(conn, "SELECT * FROM raw_projections WHERE season=? AND player_id IS NOT NULL "
                           "ORDER BY source, stage", (season,))
        names = dict(conn.execute("SELECT player_id, canonical_name FROM players").fetchall())
    if not finals:
        raise ValueError(f"Aucune projection pondérée pour {season} : lancez d'abord python main.py.")

    by_phase = {}
    for f in finals:
        by_phase.setdefault(f["phase"], []).append(f)
    weights = {c: float(league["categories"].get(c, 0) or 0) for c in CATEGORIES}
    refs, sorted_sums = {}, {}
    for phase, players in by_phase.items():
        refs[phase] = reference_params(players, league)
        sorted_sums[phase] = {m: sorted(-(p[f"z_sum_{m}"] or 0) for p in players) for m in ("avg", "tot")}
    finals_by_id = {phase: {p["player_id"]: p for p in players} for phase, players in by_phase.items()}
    main_phase = "draft" if "draft" in by_phase else next(iter(by_phase))
    order = {p["player_id"]: p["rank_tot"] for p in by_phase[main_phase]}

    def phase_of(stage):
        if stage == "draft" or stage in by_phase:
            return stage if stage in by_phase else main_phase
        return "lt" if "lt" in by_phase else main_phase

    per_game_labels = _per_game_labels()
    rows = []
    for r in raws:
        phase = phase_of(r["stage"])
        final = finals_by_id[phase].get(r["player_id"])
        if not r.get("gp") and r["source"] in per_game_labels and final and final.get("gp"):
            _games_from_final(r, final)
        estimated = _estimate_shots(r, final)
        for mode in ("avg", "tot"):
            apply_params([r], refs[phase][mode], weights, mode)
        rows.append((phase, r, estimated, final))
    for phase, players in by_phase.items():
        for p in players:
            rows.append((phase, dict(p, source=FINAL_LABEL, stage=phase), bool(p.get("fga_estimated")), p))

    def rank(phase, mode, z_sum):
        return bisect.bisect_left(sorted_sums[phase][mode], -(z_sum or 0)) + 1

    out = [HEADER]
    rows.sort(key=lambda x: (order.get(x[1]["player_id"], 10 ** 6), names.get(x[1]["player_id"], ""),
                             x[1]["source"] != FINAL_LABEL, x[1]["stage"], x[1]["source"]))
    for phase, r, estimated, final in rows:
        is_final = r["source"] == FINAL_LABEL
        z_avg = (r.get("z_sum_avg") or 0) / weight_sum
        z_tot = (r.get("z_sum_tot") or 0) / weight_sum
        out.append([
            names.get(r["player_id"]) or r.get("player"), r["source"], "" if is_final else labels.get(r["source"], ""),
            r["stage"], r.get("team") or "", r.get("positions") or "",
            round(r["gp"]) if r.get("gp") else "", _r(r.get("mpg"), 1),
            _per_game(r, "pts"), _per_game(r, "reb"), _per_game(r, "ast"), _per_game(r, "stl"),
            _per_game(r, "blk"), _per_game(r, "tov"), _per_game(r, "fg3m"),
            _per_game(r, "fgm"), _per_game(r, "fga"), _r(r.get("fgp"), 3),
            _per_game(r, "ftm"), _per_game(r, "fta"), _r(r.get("ftp"), 3),
            r.get("adp") if r.get("adp") is not None else "", "oui" if estimated else "",
            round(z_avg, 3), r["rank_avg"] if is_final else rank(phase, "avg", r.get("z_sum_avg")),
            round(z_tot, 3), r["rank_tot"] if is_final else rank(phase, "tot", r.get("z_sum_tot")),
            final["rank_tot"] if final else "",
        ])
    return out
