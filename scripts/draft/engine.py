"""Moteur de recommandation pour une draft snake en Rotisserie.

Principe
--------
En roto, chaque équipe marque des points selon son rang dans chaque catégorie (15 équipes :
1er = 15 pts, dernier = 1 pt). Le bon choix n'est donc pas le meilleur joueur « dans
l'absolu » mais celui qui fait gagner le plus de points au classement final projeté.

Pour chaque joueur candidat :
1. on simule la suite de la draft N fois. Les autres managers choisissent selon l'ADP, avec
   un bruit aléatoire ; en mode « need » (Optim | Draft | Opponent model), ils départagent
   les quelques prochains joueurs de l'ADP selon les besoins de leur équipe, d'autant plus
   que la draft avance. Nos choix suivants : meilleur z-score TOT compatible avec les postes
   à remplir ;
2. on projette les totaux de chaque équipe sur la saison, avec le plafond de matchs
   (82 x postes titulaires = 656) : les meilleurs joueurs par match jouent en priorité ;
3. on convertit les totaux en points roto « espérés » : pour chaque catégorie, la
   probabilité de devancer chaque adversaire (loi normale sur l'écart, pour tenir compte
   de l'incertitude des projections) ;
4. on compare les points espérés de notre équipe selon le candidat choisi.

Les pourcentages (FG%, FT%) sont recalculés à partir des tirs réussis / tentés de
l'équipe entière : un gros volume à faible réussite pèse vraiment sur l'équipe.
"""

import logging
import math
from dataclasses import dataclass, field

import numpy as np

from scripts.config import games_cap
from scripts.draft.pool import STATS

log = logging.getLogger(__name__)

S = {name: i for i, name in enumerate(STATS)}
CATEGORY_ORDER = ("fgp", "fg3m", "ftp", "reb", "ast", "stl", "blk", "tov", "pts")
LOWER_IS_BETTER = {"tov"}
UNCERTAINTY = 0.5   # écart-type de l'incertitude = 0,5 x dispersion des équipes dans la catégorie
_erf = np.vectorize(math.erf)


# --- Projection d'une équipe ------------------------------------------------------------

def team_totals(players, cap):
    """Totaux saison d'une équipe : les meilleurs joueurs par match jouent jusqu'au plafond."""
    totals = np.zeros(len(STATS))
    remaining = float(cap)
    for p in sorted(players, key=lambda x: x.value_avg, reverse=True):
        if remaining <= 0:
            break
        games = min(p.gp, remaining)
        totals += p.per_game * games
        remaining -= games
    return totals


def category_values(totals, categories):
    out = []
    for cat in categories:
        if cat == "fgp":
            out.append(totals[S["fgm"]] / totals[S["fga"]] if totals[S["fga"]] else 0.0)
        elif cat == "ftp":
            out.append(totals[S["ftm"]] / totals[S["fta"]] if totals[S["fta"]] else 0.0)
        else:
            out.append(totals[S[cat]])
    return np.array(out)


def roto_points(values, categories, weights):
    """values : matrice équipes x catégories -> points roto espérés (équipes x catégories)."""
    n_teams = values.shape[0]
    points = np.zeros_like(values, dtype=float)
    for j, cat in enumerate(categories):
        col = values[:, j].astype(float)
        if cat in LOWER_IS_BETTER:
            col = -col
        sigma = UNCERTAINTY * col.std()
        diff = col[:, None] - col[None, :]
        if sigma <= 1e-12:
            beat = np.where(diff > 0, 1.0, np.where(diff < 0, 0.0, 0.5))
        else:
            beat = 0.5 * (1.0 + _erf(diff / (sigma * math.sqrt(2.0))))
        np.fill_diagonal(beat, 0.0)
        points[:, j] = (1.0 + beat.sum(axis=1)) * weights[j]
    return points if n_teams else points


# --- Postes ---------------------------------------------------------------------------

def required_slots(league):
    slots = []
    for pos, n in league["roster"].items():
        pos = pos.upper()
        if pos in ("UTIL", "BN", "IL", "IL+"):
            continue
        slots += [pos] * int(n)
    return slots


def unmet_slots(players, slots):
    """Nombre de postes obligatoires (G, F, C...) qu'aucun joueur ne peut couvrir."""
    match = {}

    def assign(i, seen):
        for j, slot in enumerate(slots):
            if slot in players[i].groups and j not in seen:
                seen.add(j)
                if j not in match or assign(match[j], seen):
                    match[j] = i
                    return True
        return False

    matched = sum(assign(i, set()) for i in range(len(players)))
    return len(slots) - matched


# --- Simulation -------------------------------------------------------------------------

@dataclass
class Candidate:
    player: object
    sims: int = 0
    available_now: int = 0
    available_next: float | None = None
    points: float = 0.0
    points_cat: np.ndarray = None
    team_points: np.ndarray = None      # équipes x catégories (somme sur les simulations)
    feasible: bool = True

    @property
    def p_now(self):
        return self.available_now / self.sims if self.sims else 0.0

    @property
    def mean_points(self):
        return self.points / self.available_now if self.available_now else None

    @property
    def mean_cat(self):
        return self.points_cat / self.available_now if self.available_now else None


@dataclass
class Recommendation:
    current: int | None
    my_pick: int | None
    my_next: int | None
    categories: tuple
    candidates: list = field(default_factory=list)
    standings: np.ndarray = None         # équipes x catégories (points espérés)
    standings_now: np.ndarray = None     # classement projeté des effectifs actuels
    totals_now: np.ndarray = None        # valeurs par catégorie des effectifs actuels


class Simulator:
    def __init__(self, league, pool, state, seed=2026):
        self.league = league
        self.pool = pool
        self.state = state
        self.cap = games_cap(league)
        cats = [c for c in CATEGORY_ORDER if float(league["categories"].get(c, 0) or 0) > 0]
        self.categories = tuple(cats)
        self.weights = np.array([float(league["categories"][c]) for c in cats])
        self.slots = required_slots(league)
        self.team_index = {t: i for i, t in enumerate(state.order)}
        draft = league.get("draft", {})
        self.n_sims = max(1, int(draft.get("simulations", 40)))
        self.n_candidates = max(1, int(draft.get("candidates", 40)))
        self.noise = float(draft.get("adp_noise", 0.15))
        self.rng = np.random.default_rng(seed)
        self.value_order = sorted(range(len(pool)), key=lambda i: pool.players[i].value_tot, reverse=True)
        # modèle des choix simulés : adp (marché seul) ou need (marché + besoins de chaque équipe)
        self.model = str(draft.get("opponent_model", "need")).lower()
        self.need_weight = float(draft.get("need_weight", 2.0))
        self.need_k = max(1, int(draft.get("need_candidates", 4)))
        self.rounds = max(1, int(draft.get("rounds", 12)))
        self._init_need()

    # standings --------------------------------------------------------------------------
    def evaluate(self, rosters):
        values = np.array([category_values(team_totals(rosters[t], self.cap), self.categories)
                           for t in self.state.order])
        return values, roto_points(values, self.categories, self.weights)

    # besoins des équipes (mode need) ---------------------------------------------------------
    def _init_need(self):
        """Totaux saison par joueur (sans plafond), pour estimer vite ce qu'un joueur apporte à une équipe.

        Les équipes sont comparées sur leur moyenne par joueur (catégories de volume) et sur leurs
        pourcentages : en snake, elles n'ont pas le même nombre de joueurs au même moment.
        """
        totals = (np.array([p.per_game * p.gp for p in self.pool.players]) if len(self.pool)
                  else np.zeros((0, len(STATS))))
        self._cc = np.array([j for j, c in enumerate(self.categories) if c not in ("fgp", "ftp")], dtype=int)
        self._pc = np.array([j for j, c in enumerate(self.categories) if c in ("fgp", "ftp")], dtype=int)
        pct = [("fgm", "fga") if self.categories[j] == "fgp" else ("ftm", "fta") for j in self._pc]
        self._cnt = totals[:, [S[self.categories[j]] for j in self._cc]] if len(self._cc) else np.zeros((len(totals), 0))
        self._mk = totals[:, [S[m] for m, _ in pct]] if pct else np.zeros((len(totals), 0))
        self._at = totals[:, [S[a] for _, a in pct]] if pct else np.zeros((len(totals), 0))
        self._sign = np.array([-1.0 if c in LOWER_IS_BETTER else 1.0 for c in self.categories])

    def _need_state(self, rosters):
        """État des besoins : sommes par équipe, nombre de joueurs, valeurs par catégorie, dispersion."""
        n_teams = len(self.state.order)
        ns = {"cnt": np.zeros((n_teams, len(self._cc))), "mk": np.zeros((n_teams, len(self._pc))),
              "at": np.zeros((n_teams, len(self._pc))), "n": np.zeros(n_teams),
              "x": np.zeros((n_teams, len(self.categories))),
              "pending": [(i, p.idx) for t, i in self.team_index.items() for p in rosters.get(t, [])]}
        self._refresh_slopes(ns)
        return ns

    @staticmethod
    def _sigma(x):
        sigma = UNCERTAINTY * x.std(axis=0)
        return np.where(sigma > 1e-12, sigma, 1.0)

    def _need_flush(self, ns):
        """Intègre les choix en attente (une seule opération vectorisée par tour)."""
        if not ns["pending"]:
            return
        teams, idxs = map(list, zip(*ns["pending"]))
        ns["pending"].clear()
        np.add.at(ns["cnt"], teams, self._cnt[idxs])
        np.add.at(ns["mk"], teams, self._mk[idxs])
        np.add.at(ns["at"], teams, self._at[idxs])
        np.add.at(ns["n"], teams, 1.0)
        x = ns["x"]
        x[:, self._cc] = ns["cnt"] / np.maximum(ns["n"], 1.0)[:, None]
        x[:, self._pc] = ns["mk"] / np.maximum(ns["at"], 1e-9)

    def _refresh_slopes(self, ns):
        """Pente des points roto espérés de chaque équipe dans chaque catégorie (recalculée à chaque tour).

        Densité de l'écart avec chaque adversaire (loi normale, comme roto_points) x sens x pondération.
        """
        self._need_flush(ns)
        x = ns["x"]
        sigma = self._sigma(x)
        z = (x[:, None, :] - x[None, :, :]) / sigma
        slope = (np.exp(-0.5 * z * z).sum(axis=1) - 1.0) / sigma * self._sign * self.weights
        ns["slope"] = slope
        # gains linéarisés de tous les joueurs pour chaque équipe (joueurs x équipes), pour les choix
        # des adversaires : volumes -> écart à la moyenne de l'équipe, pourcentages -> effet sur le %
        gain = (self._cnt @ slope[:, self._cc].T - (x[:, self._cc] * slope[:, self._cc]).sum(axis=1)) \
            / (ns["n"] + 1.0)
        if len(self._pc):
            at = np.maximum(ns["at"], 1e-9)
            u = slope[:, self._pc] / at
            gain = gain + self._mk @ u.T - self._at @ (u * x[:, self._pc]).T
        ns["gain"] = gain

    def _need_weight(self, overall):
        rnd = overall // max(1, self.state.teams) + 1
        return self.need_weight * (rnd - 1) / max(1, self.rounds - 1)

    # un tirage de la suite de la draft ------------------------------------------------------
    def _market_order(self):
        market = np.array([p.market for p in self.pool.players])
        noisy = market * np.exp(self.rng.normal(0.0, self.noise, size=len(market)))
        return list(np.argsort(noisy, kind="stable"))

    def _run(self, start, taken, rosters, market_order, forced=None, stop_at_my_pick=False):
        """Déroule la draft à partir de `start`. Modifie taken / rosters en place.

        forced : {overall: idx} imposé ; stop_at_my_pick : s'arrête avant notre prochain choix.
        Retourne l'overall où l'on s'est arrêté (ou None si fin de draft).
        """
        st = self.state
        players = self.pool.players
        need = self.model == "need"
        if need:
            ns = self._need_state(rosters)
        mptr = 0
        for overall in range(start, st.total_picks):
            if overall in st.picks:
                continue
            team = st.team_at(overall)
            if team == st.my_team:
                if stop_at_my_pick:
                    return overall
                if forced and overall in forced:
                    idx = forced[overall]
                else:
                    idx = self._my_choice(taken, rosters[team], overall)
            else:
                while mptr < len(market_order) and market_order[mptr] in taken:
                    mptr += 1
                if mptr >= len(market_order):
                    return None
                idx = market_order[mptr]
                lam = self._need_weight(overall) if need else 0.0
                if lam * math.sqrt(2 * self.need_k) > 1 and self.need_k > 1:   # sinon l'ADP l'emporte toujours
                    cands, k = [], mptr
                    while k < len(market_order) and len(cands) < self.need_k:
                        if market_order[k] not in taken:
                            cands.append(market_order[k])
                        k += 1
                    if len(cands) > 1:
                        gains = ns["gain"][cands, self.team_index[team]].tolist()
                        mean = sum(gains) / len(gains)
                        spread = math.sqrt(sum((g - mean) ** 2 for g in gains) / len(gains))
                        if spread > 1e-12:
                            scores = [lam * (g - mean) / spread - r for r, g in enumerate(gains)]
                            idx = cands[scores.index(max(scores))]
            if idx is None:
                continue
            taken.add(idx)
            rosters[team].append(players[idx])
            if need:
                ns["pending"].append((self.team_index[team], idx))
                if (overall + 1) % st.teams == 0:     # pentes remises à jour à chaque tour
                    self._refresh_slopes(ns)
        return None

    def _my_choice(self, taken, roster, overall):
        """Notre choix simulé : meilleur z TOT disponible qui laisse les postes complétables.

        (Un choix « selon nos besoins » a été essayé : pas meilleur sur les mock drafts.)"""
        left_after = len(self.state.my_picks_from(overall + 1))
        players = self.pool.players
        fallback = None
        for idx in self.value_order:
            if idx in taken:
                continue
            if fallback is None:
                fallback = idx
            if unmet_slots(roster + [players[idx]], self.slots) <= left_after:
                return idx
        return fallback

    # recommandation -----------------------------------------------------------------------
    def recommend(self):
        st = self.state
        base_rosters = st.rosters()
        base_taken = st.taken()
        values_now, points_now = self.evaluate(base_rosters)
        reco = Recommendation(current=st.current, my_pick=None, my_next=None, categories=self.categories,
                              standings_now=points_now, totals_now=values_now)
        if st.current is None:
            return reco
        mine = st.my_picks_from(st.current)
        if not mine:
            return reco
        reco.my_pick = mine[0]
        reco.my_next = mine[1] if len(mine) > 1 else None

        free = [i for i in range(len(self.pool)) if i not in base_taken]
        by_value = sorted(free, key=lambda i: self.pool.players[i].value_tot, reverse=True)
        by_market = sorted(free, key=lambda i: self.pool.players[i].market)
        chosen = list(dict.fromkeys(by_value[: self.n_candidates] + by_market[: max(10, self.n_candidates // 3)]))
        my_roster_now = base_rosters[st.my_team]
        left_after = len(mine) - 1
        cands = {}
        for idx in chosen:
            p = self.pool.players[idx]
            feasible = unmet_slots(my_roster_now + [p], self.slots) <= left_after
            cands[idx] = Candidate(player=p, feasible=feasible,
                                   points_cat=np.zeros(len(self.categories)),
                                   team_points=np.zeros((st.teams, len(self.categories))))
        next_avail = {idx: 0 for idx in cands}
        next_runs = 0

        for _ in range(self.n_sims):
            market_order = self._market_order()
            taken = set(base_taken)
            rosters = {t: list(r) for t, r in base_rosters.items()}
            self._run(st.current, taken, rosters, market_order, stop_at_my_pick=True)
            # disponibilité au choix suivant : on prend le meilleur candidat, on regarde ce qui reste
            top = [i for i in by_value if i not in taken][:2]
            snapshot = {}
            if reco.my_next is not None:
                for t_idx in top:
                    t2, r2 = set(taken) | {t_idx}, {t: list(r) for t, r in rosters.items()}
                    r2[st.my_team].append(self.pool.players[t_idx])
                    self._run(reco.my_pick + 1, t2, r2, market_order, stop_at_my_pick=True)
                    snapshot[t_idx] = t2
                if top:
                    next_runs += 1
                    for idx in cands:
                        ref = top[0] if idx != top[0] or len(top) == 1 else top[1]
                        if idx != ref and idx not in snapshot[ref]:
                            next_avail[idx] += 1
            for idx, cand in cands.items():
                cand.sims += 1
                if idx in taken or not cand.feasible:
                    continue
                cand.available_now += 1
                t2, r2 = set(taken), {t: list(r) for t, r in rosters.items()}
                self._run(reco.my_pick, t2, r2, market_order, forced={reco.my_pick: idx})
                _, pts = self.evaluate(r2)
                me = self.team_index[st.my_team]
                cand.points += pts[me].sum()
                cand.points_cat += pts[me]
                cand.team_points += pts

        for idx, cand in cands.items():
            if reco.my_next is not None and next_runs:
                cand.available_next = next_avail[idx] / next_runs
        # un joueur presque jamais disponible à mon choix ne doit pas être en tête :
        # on classe d'abord ceux disponibles dans au moins 20 % des simulations
        min_avail = max(1, int(round(0.2 * self.n_sims)))
        ranked = sorted(cands.values(), key=lambda c: (c.feasible, c.available_now >= min_avail,
                                                       c.mean_points or 0), reverse=True)
        reco.candidates = ranked
        best = next((c for c in ranked if c.available_now), None)
        if best is not None:
            reco.standings = best.team_points / best.available_now
        return reco
