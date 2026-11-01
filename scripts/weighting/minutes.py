"""Minutes d'équipe : chaque source ramenée à 240 minutes par match restant (5 x 48), avant la pondération.

Les sources projettent leurs joueurs un par un : additionnées par équipe, leurs minutes dépassent souvent
ce qu'une équipe peut jouer (jusqu'à 340 min / match chez 9cat), surtout à cause du fond de banc. Pour
chaque source qui donne des minutes (MIN, ou GP x MPG) et chaque équipe NBA, sur la phase lt :

- minutes de l'équipe = somme des GP x MPG de ses joueurs, un blessé ne comptant que les matchs de son
  équipe après son retour (0 s'il est absent jusqu'à la fin de saison) ; divisée par les matchs restants
  de l'équipe (calendrier NBA) ;
- au-dessus de la cible (Optim | Minutes | Team target, 240), le surplus est retiré :
  1. aux joueurs au-delà du 10e, sans descendre ce groupe sous Deep bench floor (15 min / match en tout) ;
  2. puis, s'il en reste, aux 8e-10e ;
  les Protected players (7) premiers ne sont jamais réduits ;
- rang d'un joueur dans son équipe : son MPG réel (stats de la saison, au moins Min real games matchs),
  sinon le MPG de consensus des sources de la grille (blessé, recrue) : même ordre pour toutes les sources,
  car l'ordre propre à une source reflète mal le rôle réel (ex. un titulaire classé 11e derrière des blessés) ;
- un joueur n'est jamais ramené sous son MPG réel, ni réduit de plus de Max cut per player (30 %) dans une
  source : dans un effectif surchargé (blessés de retour), la cible peut donc ne pas être atteinte ;
- réduction = MPG, MIN et totaux multipliés par le même facteur : GP et stats par minute inchangés ;
- pas de calendrier, joueur sans équipe (FA) ou source sans minutes : rien n'est modifié.
"""

import logging
from collections import defaultdict
from datetime import date

from scripts.injuries import SEASON
from scripts.names import name_key

log = logging.getLogger(__name__)

SCALED = ("mpg", "min", "pts", "reb", "ast", "stl", "blk", "tov", "fg3m", "fg3a", "fga", "fgm", "fta", "ftm")
DEFAULTS = {"enabled": True, "target": 240.0, "protected": 7, "rotation": 10, "deep_floor": 15.0,
            "min_real_games": 3, "max_cut": 0.30}


def params(league):
    conf = dict(DEFAULTS, **{k: v for k, v in (league.get("minutes") or {}).items() if v not in (None, "")})
    conf["enabled"] = str(conf["enabled"]).strip().lower() not in ("false", "0", "non", "no", "off")
    for key in ("target", "deep_floor", "max_cut"):
        conf[key] = float(conf[key])
    if conf["max_cut"] > 1:                     # saisi en pourcentage (30) plutôt qu'en fraction (0,3)
        conf["max_cut"] /= 100
    for key in ("protected", "rotation", "min_real_games"):
        conf[key] = int(conf[key])
    conf["rotation"] = max(conf["rotation"], conf["protected"])
    return conf


def real_mpg(conn, season, min_games):
    """{player_id: MPG réel} : stats de la saison (étape sea), source au plus grand nombre de matchs."""
    best = {}
    for pid, gp, mpg, mn in conn.execute(
            "SELECT player_id, gp, mpg, min FROM raw_projections WHERE season=? AND stage='sea' "
            "AND player_id IS NOT NULL AND gp >= ?", (season, min_games)):
        value = mpg if mpg else (mn / gp if mn and gp else None)
        if value is not None and (pid not in best or gp > best[pid][0]):
            best[pid] = (gp, value)
    return {pid: v for pid, (_, v) in best.items()}


def _mpg(row):
    if row.get("mpg"):
        return row["mpg"]
    if row.get("min") and row.get("gp"):
        return row["min"] / row["gp"]
    return None


def consensus(loaded):
    """{player_id: (MPG de consensus, GP moyen)} : moyennes pondérées (niveaux MIN et GP) des sources."""
    mpg, gp = defaultdict(lambda: [0.0, 0.0]), defaultdict(lambda: [0.0, 0.0])
    for slot, data in loaded:
        for pid, row in data.items():
            m, w = _mpg(row), slot["weights"]["min"]
            if m is not None and w > 0:
                mpg[pid][0] += w * m
                mpg[pid][1] += w
            g, w = row.get("gp"), slot["weights"]["gp"]
            if g is not None:
                w = w if w > 0 else 1e-6        # GP d'une source sans poids : repli si aucune autre
                gp[pid][0] += w * g
                gp[pid][1] += w
    pids = set(mpg) | set(gp)
    return {pid: (mpg[pid][0] / mpg[pid][1] if mpg[pid][1] else None,
                  gp[pid][0] / gp[pid][1] if gp[pid][1] else None) for pid in pids}


def normalize(loaded, phase, league, games, availability=None, real=None, today=None):
    """Ramène les minutes de chaque source / équipe à la cible (modifie les lignes de `loaded` en place).

    loaded = [(slot, {player_id: ligne})]. Retourne {code source: [(équipe, minutes / match avant, après)]}."""
    from scripts.season.schedule import games_by_team, team_code

    conf = params(league)
    if not conf["enabled"] or not games:
        return {}
    today = today or date.today()
    left = {t: n for t, n in games_by_team(games, today).items() if n}
    after_return = {}
    availability = availability or {}
    real = real or {}
    cons = consensus(loaded)
    role = {pid: real.get(pid) if real.get(pid) is not None else (cons[pid][0] or 0.0) for pid in cons}
    report = {}
    for slot, data in loaded:
        if not slot["weights"]["min"] and not slot["weights"]["stats"].get("pts"):
            continue
        by_team = defaultdict(list)
        for pid, row in data.items():
            team = team_code(row.get("team") or "")
            mpg = _mpg(row)
            gp = row.get("gp") if row.get("gp") is not None else cons.get(pid, (None, None))[1]
            if team not in left or not mpg or not gp:
                continue
            back = availability.get(name_key(row.get("player") or ""))
            if back == SEASON:
                gp = 0
            elif isinstance(back, date) and back > today:
                if back not in after_return:
                    after_return[back] = games_by_team(games, back)
                gp = min(gp, after_return[back].get(team, 0))
            by_team[team].append((role.get(pid, 0.0), pid, gp * mpg, mpg))
        changed = []
        for team, players in by_team.items():
            g = left[team]
            total = sum(m for _, _, m, _ in players)
            surplus = total - conf["target"] * g
            if surplus <= 0:
                continue
            players.sort(key=lambda p: -p[0])
            mid = players[conf["protected"]:conf["rotation"]]
            deep = players[conf["rotation"]:]
            taken = _take(data, deep, surplus, conf["deep_floor"] * g, real, conf["max_cut"])
            taken += _take(data, mid, surplus - taken, 0.0, real, conf["max_cut"])
            if taken / g >= 0.5:        # moins d'une demi-minute par match : non signalé
                changed.append((team, total / g, (total - taken) / g))
        if changed:
            report[slot["code"]] = changed
    _log(phase, report, conf["target"])
    return report


def _take(data, group, wanted, floor, real, max_cut=1.0):
    """Retire jusqu'à `wanted` minutes au groupe (même facteur pour tous), sans descendre le groupe sous
    `floor` minutes, un joueur sous son MPG réel, ni réduire un joueur de plus de `max_cut`.
    Retourne les minutes retirées."""
    if wanted <= 0 or not group:
        return 0.0
    total = sum(m for _, _, m, _ in group)
    room = total - floor
    if room <= 0:
        return 0.0
    factor = 1 - min(wanted, room) / total
    taken = 0.0
    for _, pid, minutes, mpg in group:
        f = max(factor, 1 - max_cut)
        if real.get(pid):
            f = max(f, min(1.0, real[pid] / mpg))
        if f >= 1:
            continue
        row = data[pid]
        for col in SCALED:
            if row.get(col) is not None:
                row[col] = row[col] * f
        taken += minutes * (1 - f)
    return taken


def _log(phase, report, target):
    for code, teams in report.items():
        teams = sorted(teams, key=lambda t: -t[1])
        detail = ", ".join(f"{t} {before:.0f} -> {after:.0f}" for t, before, after in teams[:10])
        log.info("[Minutes %s] %s : %d équipe(s) au-dessus de %.0f min / match ramenées (banc réduit) : %s%s",
                 phase, code, len(teams), target, detail, " ..." if len(teams) > 10 else "")
