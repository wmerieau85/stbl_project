"""Projection du classement roto en fin de saison.

Pour chaque équipe :
    total final = stats réelles (classement Yahoo)
                + 15 prochains jours : phase st x matchs au calendrier
                + reste de la saison : phase lt x matchs au calendrier
Les matchs de chaque joueur = matchs de son équipe NBA x probabilité de jouer
(matchs projetés par lt / matchs restants au calendrier, au plus 1). Les matchs sont ensuite
limités par les plafonds restants de chaque poste (G, F, C, Util) : les meilleurs joueurs
par match passent en premier, sur leur poste puis en Util.

Les pourcentages sont recalculés à partir des tirs : les tentatives réelles, absentes des
pages publiques Yahoo, sont estimées avec les tentatives par match projetées de l'effectif.

Classement : points roto espérés (même modèle que la draft) et chances de finir 1er ou
dans les 3 premiers (tirages aléatoires avec la même incertitude).
"""

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from scripts.draft.engine import CATEGORY_ORDER, LOWER_IS_BETTER, UNCERTAINTY, S, category_values, roto_points
from scripts.draft.pool import STATS
from scripts.season.schedule import games_by_team, team_code

log = logging.getLogger(__name__)

BUCKETS = ("G", "F", "C", "UTIL")
_BUCKET_OF = {"PG": "G", "SG": "G", "G": "G", "SF": "F", "PF": "F", "F": "F", "C": "C"}


def buckets_of(positions):
    """'PG,SF' -> ['G', 'F'] (postes Yahoo de la ligue)."""
    out = []
    for p in str(positions or "").replace("/", ",").split(","):
        b = _BUCKET_OF.get(p.strip().upper())
        if b and b not in out:
            out.append(b)
    return out


@dataclass
class PlayerProjection:
    name: str
    nba_team: str
    positions: str
    slot: str
    found: bool
    games_sched: int = 0         # matchs restants au calendrier
    games_expected: float = 0.0  # après probabilité de jouer
    games_used: float = 0.0      # après plafonds par poste
    value: float = 0.0
    totals: np.ndarray = field(default_factory=lambda: np.zeros(len(STATS)))


@dataclass
class TeamProjection:
    manager: str
    team_name: str
    players: list
    caps: dict                    # poste -> matchs restants
    used: dict                    # poste -> matchs projetés
    actual: np.ndarray            # stats réelles (estimées pour les tirs)
    actual_gp: float
    remaining: np.ndarray
    actual_points: dict
    actual_rank: float | None

    @property
    def final(self):
        return self.actual + self.remaining


def _per_game(player):
    return player.per_game if player is not None else np.zeros(len(STATS))


def project_team(manager, team_name, roster, standing, lt_pool, st_pool, games, today, horizon_days, league):
    end_st = today + timedelta(days=horizon_days)
    sched_st = games_by_team(games, today, end_st)
    sched_lt = games_by_team(games, end_st)
    players = []
    for entry in roster["players"]:
        lt = lt_pool.find(entry["player"])
        st = st_pool.find(entry["player"]) if st_pool is not None else None
        nba = team_code(entry["nba_team"] or (lt.team if lt else ""))
        g_st, g_lt = sched_st.get(nba, 0), sched_lt.get(nba, 0)
        pp = PlayerProjection(entry["player"], nba, entry["positions"] or (lt.positions if lt else ""),
                              entry["slot"], lt is not None, games_sched=g_st + g_lt)
        if lt is not None and pp.games_sched:
            p_play = min(1.0, lt.gp / pp.games_sched) if lt.gp else 0.0
            e_st, e_lt = g_st * p_play, g_lt * p_play
            pp.games_expected = e_st + e_lt
            pp.value = lt.value_avg
            rate_st = _per_game(st if st is not None else lt)
            pp.totals = rate_st * e_st + _per_game(lt) * e_lt
        players.append(pp)

    caps = _caps(roster, league)
    used = {b: 0.0 for b in BUCKETS}
    left = dict(caps)
    for pp in sorted(players, key=lambda p: p.value, reverse=True):
        need = pp.games_expected
        if need <= 0:
            continue
        got = 0.0
        own = sorted(buckets_of(pp.positions), key=lambda b: -left.get(b, 0))
        for b in own + ["UTIL"]:
            take = min(need - got, left.get(b, 0))
            if take > 0:
                left[b] -= take
                used[b] += take
                got += take
            if got >= need - 1e-9:
                break
        pp.games_used = got
        pp.totals = pp.totals * (got / need)

    remaining = sum((p.totals for p in players), np.zeros(len(STATS)))
    actual, actual_gp = _actual_totals(standing, players)
    return TeamProjection(manager, team_name, players, caps, used, actual, actual_gp, remaining,
                          (standing or {}).get("points", {}), (standing or {}).get("rank"))


def _caps(roster, league):
    """Matchs restants par poste : page Yahoo, sinon 82 x nombre de postes (G, F, C, Util)."""
    per_slot = int(league.get("games", {}).get("per_slot", 82))
    slots = league.get("roster", {})
    default = {"G": per_slot * int(slots.get("G", 0) or 0) + per_slot * int(slots.get("PG", 0) or 0)
               + per_slot * int(slots.get("SG", 0) or 0),
               "F": per_slot * (int(slots.get("F", 0) or 0) + int(slots.get("SF", 0) or 0) + int(slots.get("PF", 0) or 0)),
               "C": per_slot * int(slots.get("C", 0) or 0),
               "UTIL": per_slot * int(slots.get("UTIL", slots.get("Util", 0)) or 0)}
    caps = {}
    for b in BUCKETS:
        g = roster.get("games", {}).get(b)
        caps[b] = float(g["remaining"]) if g and g.get("remaining") is not None else float(default[b])
    return caps


def _actual_totals(standing, players):
    """Stats réelles de l'équipe ; FGA / FTA estimés (non publiés) avec les tentatives projetées par match."""
    totals = np.zeros(len(STATS))
    stats = (standing or {}).get("stats", {})
    gp = stats.get("gp") or 0.0
    if not gp:
        return totals, 0.0
    for cat in ("fg3m", "pts", "reb", "ast", "stl", "blk", "tov"):
        totals[S[cat]] = stats.get(cat) or 0.0
    games = sum(p.games_expected for p in players) or 1.0
    fga_pg = sum(p.totals[S["fga"]] for p in players) / games if games else 0.0
    fta_pg = sum(p.totals[S["fta"]] for p in players) / games if games else 0.0
    totals[S["fga"]] = fga_pg * gp
    totals[S["fta"]] = fta_pg * gp
    totals[S["fgm"]] = (stats.get("fgp") or 0.0) * totals[S["fga"]]
    totals[S["ftm"]] = (stats.get("ftp") or 0.0) * totals[S["fta"]]
    return totals, gp


def standings_projection(teams, categories, weights, sims=2000, seed=2026):
    """Points espérés, rang et chances de titre / podium pour chaque équipe."""
    values = np.array([category_values(t.final, categories) for t in teams])
    points = roto_points(values, categories, weights)
    expected = points.sum(axis=1)
    rng = np.random.default_rng(seed)
    n = len(teams)
    first, podium = np.zeros(n), np.zeros(n)
    sigmas = np.array([UNCERTAINTY * values[:, j].std() for j in range(len(categories))])
    signs = np.array([-1.0 if c in LOWER_IS_BETTER else 1.0 for c in categories])
    for _ in range(sims):
        noisy = values + rng.normal(0.0, 1.0, values.shape) * sigmas
        ranks = (noisy * signs).argsort(axis=0).argsort(axis=0) + 1   # 1 = dernier ... n = premier
        total = (ranks * np.array(weights)).sum(axis=1)
        order = np.argsort(-total)
        first[order[0]] += 1
        podium[order[:3]] += 1
    return {"values": values, "points": points, "expected": expected,
            "p_first": first / sims, "p_podium": podium / sims}


def category_gaps(values, categories, me):
    """Pour chaque catégorie : écart avec l'équipe juste devant (gagner 1 pt) et juste derrière."""
    out = []
    for j, cat in enumerate(categories):
        col = values[:, j]
        sign = -1.0 if cat in LOWER_IS_BETTER else 1.0
        mine = col[me] * sign
        better = sorted(v * sign for i, v in enumerate(col) if i != me and v * sign > mine)
        worse = sorted((v * sign for i, v in enumerate(col) if i != me and v * sign < mine), reverse=True)
        rank = 1 + sum(1 for i, v in enumerate(col) if i != me and v * sign > mine)
        out.append({"cat": cat, "value": col[me], "rank": rank,
                    "to_gain": (better[0] - mine) if better else None,
                    "margin": (mine - worse[0]) if worse else None})
    return out
