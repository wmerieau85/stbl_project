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

Mon équipe (optimize_team) : les matchs sont ensuite réaffectés selon le gain de points roto attendu
face aux 14 autres, mesuré autour du total final projeté ; un match au gain négatif est laissé.

Classement : points roto espérés (même modèle que la draft) et chances de finir 1er ou
dans les 3 premiers (tirages aléatoires avec la même incertitude).
"""

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from scripts.draft.engine import CATEGORY_ORDER, LOWER_IS_BETTER, UNCERTAINTY, S, category_values, roto_points
from scripts.draft.pool import STATS
from scripts.names import strip_accents
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
    display: str = ""            # nom des projections (sans accents), sinon nom Yahoo sans accents
    games_sched: int = 0         # matchs restants au calendrier
    games_st_sched: int = 0      # matchs NBA au calendrier pendant l'horizon ST
    games_lt_sched: int = 0      # matchs NBA au calendrier après l'horizon ST
    games_expected: float = 0.0  # après probabilité de jouer
    games_st_expected: float = 0.0
    games_lt_expected: float = 0.0
    games_used: float = 0.0      # après plafonds par poste
    games_st_used: float = 0.0   # matchs affectés à ST après plafonds
    games_lt_used: float = 0.0   # matchs affectés à LT après plafonds
    games_past_sched: int = 0    # matchs NBA déjà joués par son équipe depuis le début de saison
    p_play: float = 0.0          # probabilité de jouer un match (matchs projetés lt / matchs restants)
    games_lost_caps: float = 0.0          # matchs non joués faute de plafond
    games_left_voluntarily: float = 0.0   # matchs écartés par l'optimisation (plafond encore libre)
    value: float = 0.0
    lt_gp: float = 0.0           # matchs projetés par la phase lt (doivent être des matchs restants)
    value_st: float | None = None  # valeur/match en phase st (forme récente), si disponible
    per_game_st: np.ndarray = field(default_factory=lambda: np.zeros(len(STATS)))
    per_game_lt: np.ndarray = field(default_factory=lambda: np.zeros(len(STATS)))
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
    lineup_rate: float = 1.0          # part des matchs attendus réellement alignés (adversaires)
    lineup: dict = field(default_factory=dict)   # calibrage du taux : observé, possible, confiance...

    @property
    def final(self):
        return self.actual + self.remaining


def _per_game(player):
    return player.per_game if player is not None else np.zeros(len(STATS))


def project_team(manager, team_name, roster, standing, lt_pool, st_pool, games, today, horizon_days, league):
    end_st = today + timedelta(days=horizon_days)
    sched_st = games_by_team(games, today, end_st)
    sched_lt = games_by_team(games, end_st)
    season_start = min((g[0] for g in games), default=today.isoformat())
    sched_past = games_by_team(games, season_start, today)
    players = []
    for entry in roster["players"]:
        lt = lt_pool.find(entry["player"])
        st = st_pool.find(entry["player"]) if st_pool is not None else None
        nba = team_code(entry["nba_team"] or (lt.team if lt else ""))
        g_st, g_lt = sched_st.get(nba, 0), sched_lt.get(nba, 0)
        pp = PlayerProjection(entry["player"], nba, entry["positions"] or (lt.positions if lt else ""),
                              entry["slot"], lt is not None,
                              display=lt.name if lt else strip_accents(entry["player"]), games_sched=g_st + g_lt,
                              games_st_sched=g_st, games_lt_sched=g_lt,
                              games_past_sched=sched_past.get(nba, 0))
        pp.per_game_st = _per_game(st if st is not None else lt)
        pp.per_game_lt = _per_game(lt)
        if lt is not None and pp.games_sched:
            p_play = min(1.0, lt.gp / pp.games_sched) if lt.gp else 0.0
            pp.p_play = p_play
            e_st, e_lt = g_st * p_play, g_lt * p_play
            pp.games_st_expected = e_st
            pp.games_lt_expected = e_lt
            pp.games_expected = e_st + e_lt
            pp.value = lt.value_avg
            pp.lt_gp = lt.gp or 0.0
            if st is not None:
                pp.value_st = st.value_avg
            rate_st = _per_game(st if st is not None else lt)
            pp.totals = rate_st * e_st + _per_game(lt) * e_lt
        players.append(pp)

    caps = _caps(roster, league)
    used = _allocate_by_value(players, caps)
    remaining = sum((p.totals for p in players), np.zeros(len(STATS)))
    actual, actual_gp = _actual_totals(standing, players)
    return TeamProjection(manager, team_name, players, caps, used, actual, actual_gp, remaining,
                          (standing or {}).get("points", {}), (standing or {}).get("rank"))


def _allocate_by_value(players, caps):
    """Lineup « standard » : les meilleurs joueurs par match d'abord, ST puis LT, sous les plafonds."""
    used = {b: 0.0 for b in BUCKETS}
    left = dict(caps)
    _allocate_period(players, "games_st_expected", "games_st_used",
                     lambda p: p.value_st if p.value_st is not None else p.value, left, used)
    _allocate_period(players, "games_lt_expected", "games_lt_used", lambda p: p.value, left, used)
    for pp in players:
        pp.games_used = pp.games_st_used + pp.games_lt_used
        pp.totals = pp.per_game_st * pp.games_st_used + pp.per_game_lt * pp.games_lt_used
        pp.games_lost_caps = max(0.0, pp.games_expected - pp.games_used)
        pp.games_left_voluntarily = 0.0
    return used


def apply_lineup_rate(team, rate):
    """Projette une équipe adverse avec son taux d'alignement : chaque match attendu n'est aligné qu'avec
    la probabilité `rate` (manager plus ou moins attentif), puis lineup standard sous les plafonds."""
    rate = max(0.0, min(1.0, float(rate)))
    team.lineup_rate = rate
    for p in team.players:
        p.games_st_expected = p.games_st_sched * p.p_play * rate
        p.games_lt_expected = p.games_lt_sched * p.p_play * rate
        p.games_expected = p.games_st_expected + p.games_lt_expected
    team.used = _allocate_by_value(team.players, team.caps)
    team.remaining = sum((p.totals for p in team.players), np.zeros(len(STATS)))


def _allocate_period(players, expected_attr, used_attr, value, left, used):
    """Affecte d'abord les matchs d'une période aux meilleurs joueurs admissibles."""
    for player in sorted(players, key=value, reverse=True):
        need = getattr(player, expected_attr)
        if need <= 0:
            continue
        got = 0.0
        own = sorted(buckets_of(player.positions), key=lambda b: -left.get(b, 0))
        for bucket in own + ["UTIL"]:
            take = min(need - got, left.get(bucket, 0))
            if take > 0:
                left[bucket] -= take
                used[bucket] += take
                got += take
            if got >= need - 1e-9:
                break
        setattr(player, used_attr, got)


GAIN_TOLERANCE = 1e-9   # deux gains plus proches sont égaux ; un gain au-dessus de -tolérance est joué
_PERIODS = (
    ("games_st_expected", "games_st_used", "per_game_st"),
    ("games_lt_expected", "games_lt_used", "per_game_lt"),
)


class _Flow:
    """Flot maximal incrémental (chemins augmentants) : joueurs -> postes -> plafonds."""

    def __init__(self):
        self.residual = {}

    def add_edge(self, start, end, capacity):
        self.residual.setdefault(start, {})[end] = self.residual.get(start, {}).get(end, 0.0) + capacity
        self.residual.setdefault(end, {}).setdefault(start, 0.0)

    def augment(self, source, sink):
        """Pousse le plus de flot possible ; les affectations déjà faites peuvent changer de poste."""
        while True:
            parent = {source: None}
            queue = deque([source])
            while queue and sink not in parent:
                node = queue.popleft()
                for neighbor, capacity in self.residual.get(node, {}).items():
                    if neighbor not in parent and capacity > 1e-9:
                        parent[neighbor] = node
                        queue.append(neighbor)
            if sink not in parent:
                return
            amount, node = float("inf"), sink
            while parent[node] is not None:
                amount = min(amount, self.residual[parent[node]][node])
                node = parent[node]
            node = sink
            while parent[node] is not None:
                self.residual[parent[node]][node] -= amount
                self.residual[node][parent[node]] += amount
                node = parent[node]

    def flow(self, start, end):
        return self.residual.get(end, {}).get(start, 0.0)


def _assign(team, units):
    """Affecte les matchs par ordre de priorité, au mieux des plafonds G / F / C / Util.

    Chaque unité (joueur, période) prend le plus de matchs possible sans retirer de matchs aux unités
    plus prioritaires ; un joueur multi-postes peut être déplacé de poste pour libérer de la place
    (optimal pour un ordre de priorité donné). Retourne {(id joueur, période): matchs}, {poste: matchs}.
    """
    flow, source, sink = _Flow(), ("source",), ("sink",)
    for bucket in BUCKETS:
        flow.add_edge(("bucket", bucket), sink, max(0.0, float(team.caps.get(bucket, 0.0))))
    for player, period in units:
        expected_attr = _PERIODS[period][0]
        node = ("unit", id(player), period)
        demand = getattr(player, expected_attr)
        for bucket in dict.fromkeys(buckets_of(player.positions) + ["UTIL"]):
            flow.add_edge(node, ("bucket", bucket), demand)
        flow.add_edge(source, node, demand)
        flow.augment(source, sink)
    games = {(id(p), period): flow.flow(source, ("unit", id(p), period)) for p, period in units}
    used = {bucket: flow.flow(("bucket", bucket), sink) for bucket in BUCKETS}
    return games, used


def optimize_team(team, opponents, categories, weights, step=0.5, iterations=10):
    """Affecte les matchs ST / LT de mon équipe selon le gain de points roto attendu face aux adversaires.

    Le gain d'un match est mesuré autour du total final projeté (pas à partir des seules stats réelles,
    où l'équipe paraît loin derrière partout et où seul le coût en TO compterait). Les matchs sont
    ensuite affectés par gain décroissant (à égalité : valeur par match), au mieux des plafonds ; un
    match au gain négatif est laissé volontairement. On recommence autour du nouveau total, et l'on
    garde la meilleure affectation, jamais moins bonne que l'affectation par valeur de départ.
    """
    if not categories or not opponents:
        return

    reference_totals = [other.final for other in opponents] + [team.final]
    reference_values = np.array([category_values(totals, categories) for totals in reference_totals])
    sigmas = UNCERTAINTY * reference_values.std(axis=0)
    opponent_values = np.array([category_values(other.final, categories) for other in opponents])
    signs = np.array([-1.0 if cat in LOWER_IS_BETTER else 1.0 for cat in categories])
    weights = np.asarray(weights, dtype=float)

    def score(totals):
        values = category_values(totals, categories)
        differences = (values - opponent_values) * signs
        beats = np.empty_like(differences)
        for j, sigma in enumerate(sigmas):
            if sigma <= 1e-12:
                beats[:, j] = np.where(differences[:, j] > 0, 1.0,
                                       np.where(differences[:, j] < 0, 0.0, 0.5))
            else:
                scale = sigma * math.sqrt(2.0)
                beats[:, j] = [0.5 * (1.0 + math.erf(float(diff) / scale))
                               for diff in differences[:, j]]
        return float(np.sum(weights * (1.0 + beats.sum(axis=0))))

    units = [(p, period) for p in team.players for period, (expected_attr, _, _) in enumerate(_PERIODS)
             if getattr(p, expected_attr) > 1e-9]

    def totals_of(games):
        totals = team.actual.copy()
        for p, period in units:
            totals += getattr(p, _PERIODS[period][2]) * games.get((id(p), period), 0.0)
        return totals

    def value_of(p, period):
        return p.value_st if period == 0 and p.value_st is not None else p.value

    # point de départ : l'affectation par valeur par match (celle de project_team, postes inclus)
    start, start_used = _assign(team, sorted(units, key=lambda u: value_of(*u), reverse=True))
    best_games, best_used, best_score = start, start_used, score(totals_of(start))
    games, previous = start, None
    for _ in range(iterations):
        current = totals_of(games)
        gains = {}
        base = score(current)
        for p, period in units:
            rate, played = getattr(p, _PERIODS[period][2]), games.get((id(p), period), 0.0)
            if played > 1e-9:   # apport réel de ses matchs actuels, ramené à un match
                gains[(id(p), period)] = (base - score(current - rate * played)) / played
            else:               # ce que rapporterait un demi-match de plus, ramené à un match
                gains[(id(p), period)] = (score(current + rate * step) - base) / step
        playable = [u for u in units if gains[(id(u[0]), u[1])] >= -GAIN_TOLERANCE]
        playable.sort(key=lambda u: (round(gains[(id(u[0]), u[1])] / GAIN_TOLERANCE), value_of(*u)), reverse=True)
        games, used = _assign(team, playable)
        candidate = score(totals_of(games))
        if candidate > best_score + GAIN_TOLERANCE:
            best_games, best_used, best_score = games, used, candidate
        signature = tuple(round(games.get((id(p), period), 0.0), 6) for p, period in units)
        if signature == previous:
            break
        previous = signature

    team.used = best_used
    for p in team.players:
        p.games_st_used = best_games.get((id(p), 0), 0.0)
        p.games_lt_used = best_games.get((id(p), 1), 0.0)
        p.games_used = p.games_st_used + p.games_lt_used
        p.totals = p.per_game_st * p.games_st_used + p.per_game_lt * p.games_lt_used
    team.remaining = totals_of(best_games) - team.actual
    _classify_unallocated_games(team)


def _classify_unallocated_games(team):
    """Sépare les matchs non affectés faute de plafond de ceux laissés par l'optimisation."""
    flow, source, sink = _Flow(), ("source",), ("sink",)
    for bucket in BUCKETS:
        flow.add_edge(("bucket", bucket), sink,
                      max(0.0, team.caps.get(bucket, 0.0) - team.used.get(bucket, 0.0)))
    demands = []
    for index, p in enumerate(team.players):
        node = ("player", index)
        demand = max(0.0, p.games_expected - p.games_used)
        demands.append((p, node, demand))
        for bucket in dict.fromkeys(buckets_of(p.positions) + ["UTIL"]):
            flow.add_edge(node, ("bucket", bucket), demand)
        flow.add_edge(source, node, demand)
    flow.augment(source, sink)
    for p, node, demand in demands:
        fits = flow.flow(source, node)          # aurait tenu dans les plafonds encore libres
        p.games_left_voluntarily = fits
        p.games_lost_caps = max(0.0, demand - fits)


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


def gp_inconsistencies(teams, tolerance=2):
    """Joueurs dont les matchs projetés en lt dépassent leurs matchs restants au calendrier :
    signe qu'une source ROS donne un total sur la saison entière et non des matchs restants."""
    return sorted(((p.display or p.name, p.lt_gp, p.games_sched) for t in teams for p in t.players
                   if p.found and p.games_sched and p.lt_gp > p.games_sched + tolerance),
                  key=lambda x: x[2] - x[1])
