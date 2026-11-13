"""Phase mt (moyen terme) : vue des projections entre J+15 et J+45, pour l'affichage seulement.

Aucune source ne projette « un mois » : chaque statistique par match est un mélange des phases déjà
calculées, st (forme récente) et lt (reste de la saison) :

    par match mt = poids st x par match st + (1 - poids st) x par match lt   (Optim | Season | Mid-term st weight)

et les matchs sont ceux de l'équipe NBA dans la fenêtre (Mid-term start / end (days), calendrier NBA), après
le retour d'un blessé, multipliés par la part des matchs restants que le joueur jouerait selon lt
(GP lt / matchs restants de son équipe depuis son retour). Totaux = par match x matchs ; pourcentages
recalculés à partir des tirs ; z-scores et rangs comme pour les autres phases.

La phase est enregistrée dans final_projections (phase « mt ») et envoyée dans l'onglet bdd avec les autres ;
ni la projection de saison, ni l'optimisation de mon lineup, ni les mouvements ne l'utilisent.
"""

import hashlib
import json
import logging
from datetime import date, timedelta

from scripts.config import load_league, load_settings
from scripts.db import get_connection

log = logging.getLogger(__name__)

PHASE = "mt"
DEFAULTS = {"enabled": True, "start_days": 15, "end_days": 45, "st_weight": 0.5}
BLENDED = ("pts", "reb", "ast", "stl", "blk", "tov", "fg3m", "fg3a", "fga", "fgm", "fta", "ftm", "min")


def params(league):
    conf = dict(DEFAULTS, **{k: v for k, v in ((league.get("season") or {}).get("midterm") or {}).items()
                             if v not in (None, "")})
    conf["enabled"] = str(conf["enabled"]).strip().lower() not in ("false", "0", "non", "no", "off")
    conf["start_days"], conf["end_days"] = int(conf["start_days"]), int(conf["end_days"])
    weight = float(conf["st_weight"])
    conf["st_weight"] = min(1.0, max(0.0, weight / 100 if weight > 1 else weight))   # 50 ou 0,5
    return conf


def _rows(conn, phase, season):
    cursor = conn.execute("SELECT * FROM final_projections WHERE phase=? AND season=?", (phase, season))
    names = [d[0] for d in cursor.description]
    return {r[names.index("player_id")]: dict(zip(names, r)) for r in cursor.fetchall()}


def _per_game(row):
    gp = row.get("gp") or 0
    if gp <= 0:
        return None
    out = {stat: (row.get(stat) or 0.0) / gp for stat in BLENDED}
    out["mpg"] = row.get("mpg") or (out["min"] if out["min"] else 0.0)
    return out


def blend(lt, st, weight):
    """Stats par match mélangées ; une seule phase disponible : elle seule."""
    a, b = _per_game(lt) if lt else None, _per_game(st) if st else None
    if a is None:
        return b
    if b is None:
        return a
    return {k: weight * b[k] + (1 - weight) * a[k] for k in a}


def build(lt_rows, st_rows, games, availability, conf, today):
    """Lignes de la phase mt (avant z-scores) : {colonne: valeur}."""
    from scripts.injuries import SEASON
    from scripts.names import name_key
    from scripts.season.schedule import games_by_team, team_code

    start, end = today + timedelta(days=conf["start_days"]), today + timedelta(days=conf["end_days"] + 1)
    cache = {}

    def count(first, last):
        key = (first, last)
        if key not in cache:
            cache[key] = games_by_team(games, first, last)
        return cache[key]

    results = []
    for pid in set(lt_rows) | set(st_rows):
        lt, st = lt_rows.get(pid), st_rows.get(pid)
        base = lt or st
        back = availability.get(name_key(base["player"] or ""))
        if back == SEASON:
            continue
        first = max(today, back) if isinstance(back, date) else today
        team = team_code(base.get("team") or "")
        left = count(first, None).get(team, 0)
        in_window = count(max(start, first), end).get(team, 0) if first < end else 0
        per_game = blend(lt, st, conf["st_weight"])
        if not left or not in_window or per_game is None:
            continue
        share = min(1.0, (lt or st).get("gp", 0) / left) if (lt or st).get("gp") else 0.0
        gp = in_window * share
        if gp <= 0:
            continue
        r = {"phase": PHASE, "season": base["season"], "player_id": pid, "player": base["player"],
             "team": base["team"], "positions": base["positions"], "gp": gp, "mpg": per_game["mpg"],
             "adp": None, "auction_cost": None, "auction_value": None,
             "n_sources": int(lt is not None) + int(st is not None),
             "sources": ",".join(p for p, row in (("lt", lt), ("st", st)) if row),
             "gp_coverage": None, "min_coverage": None, "stats_coverage": None, "fga_estimated": 0}
        for stat in BLENDED:
            r[stat] = per_game[stat] * gp
        r["fgp"] = r["fgm"] / r["fga"] if r["fga"] else None
        r["ftp"] = r["ftm"] / r["fta"] if r["fta"] else None
        r["fg3p"] = r["fg3m"] / r["fg3a"] if r.get("fg3a") else None
        results.append(r)
    return results


def compute(settings=None, today=None):
    """Calcule et enregistre la phase mt à partir des phases lt et st. Retourne le nombre de joueurs."""
    from scripts.injuries import season_end_as_out
    from scripts.weighting import engine

    settings = settings or load_settings()
    league = load_league()
    conf = params(league)
    if not conf["enabled"]:
        return 0
    season = settings["active_season"]
    lt_phase = (league.get("season") or {}).get("lt_phase") or "lt"
    st_phase = (league.get("season") or {}).get("st_phase") or "st"
    with get_connection() as conn:
        engine.ensure_table(conn)
        lt_rows, st_rows = _rows(conn, lt_phase, season), _rows(conn, st_phase, season)
    if not lt_rows and not st_rows:
        log.warning("[Pondération %s] Phases %s et %s absentes : moyen terme non calculé.", PHASE, lt_phase, st_phase)
        return 0
    games = engine._games(PHASE, season, settings)
    if not games:
        log.warning("[Pondération %s] Calendrier NBA indisponible : moyen terme non calculé.", PHASE)
        return 0
    availability = engine.load_availability(lt_phase, settings, league) \
        if lt_phase in engine.injury_phases(league) else {}
    availability = season_end_as_out(availability, games)
    today = today or date.today()
    results = build(lt_rows, st_rows, games, availability, conf, today)
    if not results:
        log.warning("[Pondération %s] Aucun joueur : moyen terme non calculé.", PHASE)
        return 0
    digest = hashlib.sha1(json.dumps(conf, sort_keys=True).encode()).hexdigest()[:10]
    engine.store_phase(PHASE, season, results, league, digest, settings)
    log.info("[Pondération %s] Moyen terme (J+%d à J+%d, forme récente %d %%) : %d joueurs, vue seulement.",
             PHASE, conf["start_days"], conf["end_days"], round(100 * conf["st_weight"]), len(results))
    return len(results)
