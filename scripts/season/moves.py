"""Simulation de mouvements : échanges avec un manager ou joueurs libres (onglet moves_reco).

Réglages de l'onglet settings :
- Moves | Player 1..4 : manager | joueur. Le manager est soit le mien (je cède ces joueurs), soit celui
  de Moves | Compare (je reçois ces joueurs). Ligne sans joueur : ignorée.
- Moves | Compare : « trade » (voir resolve_compare : avec mes joueurs et sans manager, recherche auto chez
  tous les managers autour de ces joueurs), « manager » + nom du manager (échange), « free agent » (je lâche
  mes joueurs),
  « auto » (+ nom du manager facultatif) : recherche d'échanges sans joueur saisi (Moves | Player ignorés), ou
  « auto free agent » : recherche de joueurs libres sans joueur saisi (Moves | Player ignorés).
- Optim | Moves : tolérance de valeur, nombre de résultats...

Recherche auto : tous les échanges 1 contre 1 à N contre N (Auto max players) avec chaque manager (ou celui
indiqué) sont filtrés sur la tolérance de valeur, puis passés à un crible rapide (stats de fin de saison
modifiées sans réoptimiser les lineups mais en respectant les plafonds de matchs, voir _Side ; points roto
de moi et du partenaire). Les plus prometteurs, parmi ceux que le partenaire a intérêt à accepter, sont
simulés exactement par lots (Auto shortlist répartis entre tailles d'échange, au plus 2 x Auto per manager
par manager, jusqu'à 4 lots tant que Min results ne sont pas atteints) ; on garde ceux qui me font gagner
au moins Min gain et
où son gain dépasse Auto min partner gain, un seul par effet sur mon équipe (le plus équitable), triés par
gain roto pour moi puis du plus équitable au moins équitable (au plus Auto per manager par manager),
complétés jusqu'à Min results par ceux où l'autre manager perd des points.

Échange : autant de joueurs reçus que cédés ; toutes les combinaisons de l'effectif d'en face (ou du
mien) sont testées, celles dont la valeur s'écarte de plus de la tolérance sont écartées, les autres
classées par gain de points roto attendus pour moi (le gain de l'autre manager est indiqué).
Joueurs libres : chaque joueur libre est testé seul à la place des joueurs lâchés, puis (plusieurs
joueurs lâchés) les meilleurs sont combinés.
Joueurs libres, recherche auto : chacun de mes joueurs (hors postes IL : lâcher un blessé ne libère pas de
place) contre chacun des meilleurs joueurs libres, au crible rapide (comme la recherche auto d'échanges) ;
les plus prometteurs (2 joueurs lâchés au plus par joueur libre, Free agent shortlist en tout) sont simulés
exactement ; seuls ceux qui me font gagner au moins Min gain sont gardés, un par joueur libre (le meilleur joueur à
lâcher), du plus grand au plus petit gain. Pour chaque joueur libre affiché, les autres joueurs à lâcher
presque aussi bons au crible (à 0,5 pt, 3 au plus) sont simulés aussi ; à gain presque égal (0,1 pt), on lâche
celui de plus faible valeur (le moins utile en remplaçant) et les autres sont cités (« ou X, même gain »).
Un joueur libre que mon lineup n'alignerait jamais n'est pas proposé : son gain ne viendrait que d'un autre
lineup pour mes joueurs.
Min gain (recherches auto) : un gain plus petit n'est pas affiché ; deux lineups presque équivalents peuvent
en effet donner quelques centièmes de point à un mouvement qui ne fait que changer ma rotation (joueur lâché
qui ne jouait pas, joueur reçu qui ne jouera pas).

Valeur d'un joueur : valeur par match au-dessus du niveau de remplacement (joueur classé au rang
« nombre de joueurs dans les effectifs », 180 pour 15 x 12 ; st sur l'horizon court, lt ensuite) x matchs
attendus d'ici la fin de saison ; jamais négative (un joueur sous ce niveau vaut 0). Les deux équipes concernées sont reprojetées (même compteur de matchs par poste, mêmes
stats réelles : elles restent acquises à l'équipe), mon lineup est réoptimisé, l'autre équipe garde son
taux d'alignement.
"""

import itertools
import logging
from dataclasses import dataclass, field
from math import comb

import numpy as np

from scripts.draft.engine import LOWER_IS_BETTER, UNCERTAINTY, S, category_values, roto_points
from scripts.names import name_key
from scripts.season.engine import BUCKETS, apply_lineup_rate, optimize_team, project_team, standings_projection

log = logging.getLogger("stbl.season.moves")

MAX_PLAYERS = 4
FA_SCREEN = 150          # joueurs libres testés un par un (les meilleurs en valeur totale)
MAX_COMBINATIONS = 20000
AUTO_FREE_AGENT = "auto free agent"
IL_SLOTS = {"IL", "IL+"}   # postes blessés : lâcher ce joueur ne libère pas de place pour un joueur libre


class MovesError(ValueError):
    pass


@dataclass
class Move:
    give: list                       # PlayerProjection cédés / lâchés
    recv: list                       # PlayerProjection reçus / ajoutés
    value_give: float
    value_recv: float
    my_points: float
    my_gain: float
    partner_gain: float | None
    rank: int
    cat_delta: np.ndarray
    teams: list = field(repr=False, default_factory=list)
    recv_games: float = 0.0          # matchs que je ferais jouer aux joueurs reçus (lineup optimisé)
    recv_detail: list = field(default_factory=list)      # [(nom, alignés, attendus, alignés st, alignés lt)]
    stat_delta: np.ndarray = None    # écart de mes stats finales par catégorie (FG% en fraction, REB en unités...)
    lineup_changes: list = field(default_factory=list)   # [(nom, écart de matchs alignés)] mes autres joueurs
    out_of_tolerance: bool = False
    trade_give: float = 0.0          # valeur servant à l'équilibre de l'échange (perçue, ou projetée)
    trade_recv: float = 0.0
    perceived_give: float | None = None   # valeur perçue (affichage), sans données réelles : None
    perceived_recv: float | None = None
    p_first: float | None = None
    p_podium: float | None = None
    partner_name: str = ""
    screen_gain: float | None = None     # recherche auto : gain estimé par le crible rapide
    partner_loses: bool = False          # recherche auto : ajouté pour le minimum, l'autre manager perd
    alternatives: list = field(default_factory=list)   # joueurs libres auto : autres [joueurs lâchés], même gain

    @property
    def value_diff(self):
        return _value_diff(self.value_give, self.value_recv)

    @property
    def trade_diff(self):
        """Écart de valeur vu par les managers (Value basis) : équilibre de l'échange."""
        return _value_diff(self.trade_give, self.trade_recv)


def replacement_level(pool, rostered):
    """Valeur par match du joueur classé au rang `rostered` (dernier joueur d'effectif)."""
    if pool is None or not pool.players:
        return None
    values = sorted((pl.value_avg for pl in pool.players if (pl.gp or 0) > 0), reverse=True)
    return values[min(max(rostered, 1), len(values)) - 1] if values else None


def player_value(p, repl_lt=0.0, repl_st=None):
    """Valeur du joueur pour le reste de la saison, au-dessus du niveau de remplacement (indépendante du
    taux d'alignement de son équipe)."""
    if not p.found or not p.p_play:
        return 0.0
    if p.value_st is not None:
        st = p.value_st - (repl_st if repl_st is not None else repl_lt)
    else:
        st = p.value - repl_lt
    lt = p.value - repl_lt
    return max(0.0, st * p.games_st_sched + lt * p.games_lt_sched) * p.p_play


def _value_diff(give, recv):
    """Écart relatif de valeur (reçue - cédée), rapporté à la plus grande des deux en valeur absolue."""
    scale = max(abs(give), abs(recv))
    return 0.0 if scale < 1e-9 else (recv - give) / scale


def _entry(p):
    return {"player": p.name, "player_id": "", "nba_team": p.nba_team, "positions": p.positions,
            "slot": p.slot, "status": ""}


def _fa_entry(player):
    return {"player": player.name, "player_id": "", "nba_team": player.team or "", "positions": player.positions or "",
            "slot": "BN", "status": ""}


def _keys(p):
    return {k for k in (name_key(p.name), name_key(p.display)) if k}


class Simulator:
    def __init__(self, result, league):
        if "context" not in result:
            raise MovesError("Projection de saison incomplète (contexte absent).")
        self.ctx = result["context"]
        self.teams = result["teams"]
        self.cats = result["categories"]
        self.weights = result["weights"]
        self.league = league
        self.base_values = np.array([category_values(t.final, self.cats) for t in self.teams])
        self.base_points = roto_points(self.base_values, self.cats, self.weights)
        self.base_total = self.base_points.sum(axis=1)
        self.evaluated = 0
        rostered = sum(len(t.players) for t in self.teams)
        self.repl_lt = replacement_level(self.ctx["lt_pool"], rostered) or 0.0
        self.repl_st = replacement_level(self.ctx["st_pool"], rostered)
        conf = league.get("moves") or {}
        self.basis = str(conf.get("value_basis") or PERCEIVED).strip().lower()
        self.min_games = max(1, int(conf.get("perceived_min_games", 5) or 5))
        season_pool = self.ctx.get("season_pool")
        self.repl_real = replacement_level(season_pool, rostered) if season_pool is not None else None
        self.real = {name_key(pl.name): pl for pl in season_pool.players} if season_pool is not None else {}

    def value(self, p):
        """Valeur projetée (mon intérêt) : st puis lt, au-dessus du niveau de remplacement."""
        return player_value(p, self.repl_lt, self.repl_st)

    def perceived(self, p):
        """Valeur vue par les managers : production réelle par match de la saison (phase season, comme les
        moyennes Yahoo) x matchs à venir. Les matchs déjà manqués ne comptent pas (un joueur revenu de
        blessure est coté sur ce qu'il produit), une blessure actuelle si (matchs à venir de la phase lt).
        Moins de Perceived min games matchs joués : moyenne réelle mélangée à la projection par match."""
        if not p.found or not p.p_play:
            return 0.0
        projected = p.value - self.repl_lt
        real = self.real.get(name_key(p.name)) or self.real.get(name_key(p.display or ""))
        if real is not None and (real.gp or 0) > 0 and self.repl_real is not None:
            weight = min(1.0, (real.gp or 0) / self.min_games)
            per_game = weight * (real.value_avg - self.repl_real) + (1 - weight) * projected
        else:
            per_game = projected
        return max(0.0, per_game * (p.games_st_sched + p.games_lt_sched) * p.p_play)

    def trade_value(self, p):
        """Valeur pour l'équilibre d'un échange (Optim | Moves | Value basis) : perçue, sinon projetée."""
        if self.basis == PERCEIVED and self.repl_real is not None:
            return self.perceived(p)
        return self.value(p)

    def index(self, manager):
        for i, t in enumerate(self.teams):
            if t.manager.casefold() == str(manager or "").strip().casefold():
                return i
        raise MovesError(f"Manager « {manager} » absent des effectifs Yahoo "
                         f"({', '.join(t.manager for t in self.teams)}).")

    def find_on(self, team, name):
        key = name_key(name)
        target = self.ctx["lt_pool"].find(name)
        target_key = name_key(target.name) if target is not None else None
        for p in team.players:
            keys = _keys(p)
            if key in keys or (target_key and target_key in keys):
                return p
        raise MovesError(f"« {name} » absent de l'effectif de {team.manager} : "
                         f"{', '.join(p.display or p.name for p in team.players)}.")

    def rebuild(self, base, entries):
        ctx = self.ctx
        roster = {"players": entries, "games": {b: {"remaining": c} for b, c in base.caps.items()}}
        team = project_team(base.manager, base.team_name, roster, None, ctx["lt_pool"], ctx["st_pool"], ctx["games"],
                            ctx["today"], ctx["horizon"], ctx["league"])
        team.actual, team.actual_gp = base.actual.copy(), base.actual_gp
        team.actual_points, team.actual_rank, team.lineup = base.actual_points, base.actual_rank, base.lineup
        return team

    def evaluate(self, me, my_entries, partner=None, partner_entries=None):
        """(équipes, points par catégorie) après le mouvement."""
        teams = list(self.teams)
        if partner is not None:
            other = self.rebuild(self.teams[partner], partner_entries)
            apply_lineup_rate(other, self.teams[partner].lineup_rate)
            teams[partner] = other
        mine = self.rebuild(self.teams[me], my_entries)
        teams[me] = mine
        optimize_team(mine, [t for i, t in enumerate(teams) if i != me], self.cats, self.weights)
        values = np.array([category_values(t.final, self.cats) for t in teams])
        self.evaluated += 1
        return teams, roto_points(values, self.cats, self.weights)

    def move(self, me, give, recv, teams, points, partner=None):
        total = points.sum(axis=1)
        rank = 1 + int(sum(1 for i, v in enumerate(total) if i != me and v > total[me] + 1e-9))
        partner_gain = float(total[partner] - self.base_total[partner]) if partner is not None else None
        received = {r.name for r in recv}
        given = {g.name for g in give}
        mine = teams[me].players
        detail = [(x.display or x.name, x.games_used, x.games_expected, x.games_st_used, x.games_lt_used)
                  for x in mine if x.name in received]
        before = {x.name: x.games_used for x in self.teams[me].players}
        changes = sorted(((x.display or x.name, x.games_used - before[x.name]) for x in mine
                          if x.name in before and x.name not in received and x.name not in given
                          and abs(x.games_used - before[x.name]) >= 1.0), key=lambda c: c[1])
        move = Move(give, recv, sum(self.value(p) for p in give), sum(self.value(p) for p in recv),
                    float(total[me]), float(total[me] - self.base_total[me]), partner_gain, rank,
                    points[me] - self.base_points[me], teams, recv_games=sum(d[1] for d in detail),
                    recv_detail=detail, lineup_changes=changes,
                    stat_delta=category_values(teams[me].final, self.cats) - self.base_values[me])
        move.trade_give = sum(self.trade_value(p) for p in give)
        move.trade_recv = sum(self.trade_value(p) for p in recv)
        if self.repl_real is not None:
            move.perceived_give = sum(self.perceived(p) for p in give)
            move.perceived_recv = sum(self.perceived(p) for p in recv)
        return move

    def rank_of(self, me):
        total = self.base_total
        return 1 + int(sum(1 for i, v in enumerate(total) if i != me and v > total[me] + 1e-9))


def _check_count(n_items, k, what):
    total = comb(n_items, k)
    if total > MAX_COMBINATIONS:
        raise MovesError(f"{total} combinaisons à tester pour {what} : réduisez le nombre de joueurs.")
    return total


def plan(league):
    """Lecture des réglages : (manager des joueurs listés, joueurs, type de comparaison, manager comparé)."""
    moves = league.get("moves") or {}
    listed = [e for e in (moves.get("players") or []) if str(e.get("player") or "").strip()]
    compare = moves.get("compare") or {}
    me = league.get("draft", {}).get("my_team")
    if not listed:
        raise MovesError("Aucun joueur dans Moves | Player 1 à 4 (onglet settings).")
    if len(listed) > MAX_PLAYERS:
        raise MovesError(f"{len(listed)} joueurs dans Moves | Player : {MAX_PLAYERS} au maximum.")
    sides = {str(e.get("manager") or me).strip().casefold() for e in listed}
    if len(sides) > 1:
        raise MovesError("Moves | Player : tous les joueurs doivent appartenir au même manager.")
    side = next(e.get("manager") or me for e in listed)
    kind = str(compare.get("type") or "").strip().lower()
    other = str(compare.get("manager") or "").strip()
    if kind not in ("manager", "free agent"):
        raise MovesError("Moves | Compare : indiquez « manager » + nom du manager, ou « free agent ».")
    if kind == "manager" and not other:
        raise MovesError("Moves | Compare : nom du manager absent (3e colonne).")
    mine = side.casefold() == str(me or "").casefold()
    if kind == "free agent" and not mine:
        raise MovesError(f"Moves | Compare = free agent : les joueurs listés doivent être les miens ({me}), "
                         f"pas ceux de {side}.")
    if kind == "manager" and not mine and side.casefold() != other.casefold():
        raise MovesError(f"Moves | Player : manager « {side} », attendu {me} ou {other} (Moves | Compare).")
    if kind == "manager" and other.casefold() == str(me or "").casefold():
        raise MovesError("Moves | Compare : le manager comparé ne peut pas être le mien.")
    return {"me": me, "side": side, "mine": mine, "players": [e["player"] for e in listed], "kind": kind,
            "other": other}


def _auto_plan(league):
    compare = (league.get("moves") or {}).get("compare") or {}
    if str(compare.get("type") or "").strip().lower() != "auto":
        return None
    me = league.get("draft", {}).get("my_team")
    other = str(compare.get("manager") or "").strip()
    if other and other.casefold() == str(me or "").casefold():
        raise MovesError("Moves | Compare : le manager comparé ne peut pas être le mien.")
    players = [str(n).strip() for n in compare.get("players") or [] if str(n).strip()]
    return {"me": me, "side": me, "mine": True, "players": players, "kind": "auto", "other": other}


def _auto_free_agent_plan(league):
    compare = (league.get("moves") or {}).get("compare") or {}
    if str(compare.get("type") or "").strip().lower() != AUTO_FREE_AGENT:
        return None
    me = league.get("draft", {}).get("my_team")
    return {"me": me, "side": me, "mine": True, "players": [], "kind": AUTO_FREE_AGENT, "other": ""}


TRADE = "trade"


def resolve_compare(league):
    """Types simplifiés de Moves | Compare, ramenés aux modes internes (ligue copiée si besoin) :
    trade : avec joueurs saisis -> manager (échange autour d'eux ; manager visé = Valeur, ou celui des
    joueurs listés s'ils ne sont pas à moi) ; mes joueurs sans manager visé -> auto chez tous les managers,
    limitée aux échanges où je cède un ou plusieurs de ces joueurs ; sans joueur -> auto (Valeur = un
    manager ou vide = tous) ;
    free agent : avec joueurs -> free agent (je les lâche), sans joueur -> auto free agent.
    Les anciens types (manager, auto, auto free agent) gardent leur sens."""
    moves = league.get("moves") or {}
    compare = moves.get("compare") or {}
    kind = str(compare.get("type") or "").strip().lower()
    listed = [e for e in (moves.get("players") or []) if str(e.get("player") or "").strip()]
    other = str(compare.get("manager") or "").strip()
    if kind == TRADE:
        if not listed:
            compare = {"type": "auto", "manager": other}
        else:
            me = str(league.get("draft", {}).get("my_team") or "")
            side = str(listed[0].get("manager") or me).strip()
            if not other and side.casefold() != me.casefold():
                other = side
            if other:
                compare = {"type": "manager", "manager": other}
            else:      # mes joueurs sans manager visé : recherche auto chez tous, autour de ces joueurs
                compare = {"type": "auto", "manager": "", "players": [e["player"] for e in listed]}
    elif kind == "free agent" and not listed:
        compare = {"type": AUTO_FREE_AGENT, "manager": ""}
    else:
        return league
    return dict(league, moves=dict(moves, compare=compare))


def simulate(result, league):
    """Mouvements classés : {mode, plan, moves, tested, kept, base...}."""
    league = resolve_compare(league)
    p = _auto_plan(league) or _auto_free_agent_plan(league) or plan(league)
    sim = Simulator(result, league)
    conf = league.get("moves") or {}
    me = sim.index(p["me"])
    if p["kind"] == "auto":
        out = _auto(sim, me, p, conf)
    elif p["kind"] == AUTO_FREE_AGENT:
        out = _auto_free_agents(sim, me, conf)
    elif p["kind"] == "manager":
        out = _trades(sim, me, p, conf)
    else:
        out = _free_agents(sim, me, p, conf)
    sims = int(league.get("season", {}).get("simulations", 2000))
    for m in out["moves"]:
        proj = standings_projection(m.teams, sim.cats, sim.weights, sims=sims)
        m.p_first, m.p_podium = float(proj["p_first"][me]), float(proj["p_podium"][me])
    proj = result.get("projection") or {}
    out.update(plan=p, me=me, categories=sim.cats, today=sim.ctx["today"], evaluated=sim.evaluated,
               base_points=float(sim.base_total[me]), base_rank=sim.rank_of(me),
               base_first=float(proj["p_first"][me]) if "p_first" in proj else None,
               base_podium=float(proj["p_podium"][me]) if "p_podium" in proj else None,
               my_team=sim.teams[me])
    return out


MIN_GAIN = 0.25    # Optim | Moves | Min gain : gain roto minimum d'un mouvement des recherches auto


def min_move_gain(conf):
    """Gain minimum (pts roto) pour garder un mouvement des recherches auto ; jamais sous 0 (un mouvement
    affiché doit me faire gagner)."""
    value = conf.get("min_gain")
    try:
        value = MIN_GAIN if value in (None, "") else float(str(value).replace(",", "."))
    except ValueError:
        value = MIN_GAIN
    return max(value, 1e-6)


PERCEIVED, PROJECTED = "perceived", "projected"     # Optim | Moves | Value basis
PERCEIVED_COLUMNS = ("Valeur perçue cédée", "Valeur perçue reçue", "Écart perçu (%)")


def display_order(m):
    """Ordre d'affichage des échanges : meilleur gain roto pour moi d'abord (arrondi au centième de point),
    puis, à gain égal, le plus équitable (plus petit écart de valeur)."""
    return (-round(m.my_gain, 2), abs(m.trade_diff))


def _trades(sim, me, p, conf):
    partner = sim.index(p["other"])
    side = me if p["mine"] else partner
    fixed = [sim.find_on(sim.teams[side], name) for name in p["players"]]
    if len({id(x) for x in fixed}) < len(fixed):
        raise MovesError("Moves | Player : le même joueur est listé deux fois.")
    k = len(fixed)
    pool_team = sim.teams[partner if p["mine"] else me]
    _check_count(len(pool_team.players), k, f"l'effectif de {pool_team.manager}")
    tolerance = float(conf.get("value_tolerance", 0.10))
    limit = int(conf.get("max_results", 20))
    minimum = min(int(conf.get("min_results", 10)), limit)
    candidates = []
    for combo in itertools.combinations(pool_team.players, k):
        give, recv = (fixed, list(combo)) if p["mine"] else (list(combo), fixed)
        vg, vr = sum(sim.trade_value(x) for x in give), sum(sim.trade_value(x) for x in recv)
        candidates.append((abs(_value_diff(vg, vr)), give, recv))
    candidates.sort(key=lambda c: c[0])

    def evaluate(give, recv):
        gone_me, gone_partner = {id(x) for x in give}, {id(x) for x in recv}
        my_entries = [_entry(x) for x in sim.teams[me].players if id(x) not in gone_me] + [_entry(x) for x in recv]
        partner_entries = ([_entry(x) for x in sim.teams[partner].players if id(x) not in gone_partner]
                           + [_entry(x) for x in give])
        teams, points = sim.evaluate(me, my_entries, partner, partner_entries)
        return sim.move(me, give, recv, teams, points, partner)

    inside = [c for c in candidates if c[0] <= tolerance + 1e-9]
    # à valeur équivalente : les meilleurs gains pour moi ; trop peu : les plus proches en valeur, hors tolérance
    moves = sorted((evaluate(g, r) for _, g, r in inside), key=lambda m: -m.my_gain)[:limit]
    for _, give, recv in candidates[len(inside):len(inside) + max(0, minimum - len(moves))]:
        move = evaluate(give, recv)
        move.out_of_tolerance = True
        moves.append(move)
    moves.sort(key=display_order)
    log.info("[Moves] %d combinaisons, %d dans la tolérance de ±%.0f %%, %d affichées.", len(candidates),
             len(inside), 100 * tolerance, len(moves))
    return {"mode": "manager", "partner": partner, "partner_team": sim.teams[partner], "fixed": fixed,
            "tested": len(candidates), "kept": len(inside), "tolerance": tolerance, "moves": moves}


def _free_agents(sim, me, p, conf):
    my_team = sim.teams[me]
    dropped = [sim.find_on(my_team, name) for name in p["players"]]
    if len({id(x) for x in dropped}) < len(dropped):
        raise MovesError("Moves | Player : le même joueur est listé deux fois.")
    k = len(dropped)
    gone = {id(x) for x in dropped}
    kept_entries = [_entry(x) for x in my_team.players if id(x) not in gone]
    free = _free_pool(sim)
    limit = int(conf.get("fa_results", 6))
    shortlist_size = max(int(conf.get("fa_shortlist", 15)), k, limit)

    def run(players):
        entries = kept_entries + [_fa_entry(pl) for pl in players]
        teams, points = sim.evaluate(me, entries)
        added = [x for x in teams[me].players if x.name in {pl.name for pl in players}]
        return sim.move(me, dropped, added, teams, points)

    singles = sorted(((run([pl]), pl) for pl in free), key=lambda x: (-round(x[0].my_gain, 3), -x[0].value_recv))
    if k == 1:
        moves = [m for m, _ in singles]
        tested = len(free)
    else:
        shortlist = [pl for _, pl in singles[:shortlist_size]]
        _check_count(len(shortlist), k, "les meilleurs joueurs libres")
        moves = [run(list(c)) for c in itertools.combinations(shortlist, k)]
        tested = len(free) + len(moves)
    moves.sort(key=lambda m: (-round(m.my_gain, 3), -m.value_recv))
    log.info("[Moves] %d joueurs libres testés (%d évaluations).", len(free), sim.evaluated)
    return {"mode": "free agent", "partner": None, "partner_team": None, "fixed": dropped, "tested": tested,
            "kept": len(moves), "tolerance": None, "moves": moves[:limit], "free_count": len(free)}


def _free_pool(sim):
    """Joueurs libres : projections lt absentes de tous les effectifs, les meilleurs en valeur totale."""
    taken = set().union(*(_keys(x) for t in sim.teams for x in t.players))
    free = [pl for pl in sim.ctx["lt_pool"].players if name_key(pl.name) not in taken and (pl.gp or 0) > 0]
    free.sort(key=lambda pl: -(pl.value_tot or 0.0))
    if not free:
        raise MovesError("Aucun joueur libre avec des projections.")
    return free[:FA_SCREEN]


# --- Recherche auto ---------------------------------------------------------------------------

def _erf_fast(x):
    """erf vectorisé (Abramowitz-Stegun 7.1.26, erreur < 1,5e-7) : suffisant pour le crible."""
    sign = np.sign(x)
    x = np.abs(x)
    t = 1.0 / (1.0 + 0.3275911 * x)
    y = 1.0 - (((((1.061405429 * t - 1.453152072) * t) + 1.421413741) * t - 0.284496735) * t
               + 0.254829592) * t * np.exp(-x * x)
    return sign * y


def _rows_points(values, rows, cats, weights):
    """values : (N, équipes, catégories) -> points roto espérés des équipes `rows` (N, len(rows))."""
    values = values.astype(float).copy()
    for j, cat in enumerate(cats):
        if cat in LOWER_IS_BETTER:
            values[:, :, j] = -values[:, :, j]
    sigma = UNCERTAINTY * values.std(axis=1)                         # (N, catégories)
    sigma = np.where(sigma <= 1e-12, 1e-12, sigma)
    out = []
    for r in rows:
        diff = values[:, r:r + 1, :] - values                         # (N, équipes, catégories)
        beat = 0.5 * (1.0 + _erf_fast(diff / (sigma[:, None, :] * np.sqrt(2.0))))
        beat[:, r, :] = 0.0
        out.append(((1.0 + beat.sum(axis=1)) * np.asarray(weights)).sum(axis=1))
    return np.stack(out, axis=1)


def _values_batch(totals, cats):
    """category_values sur une matrice (N, stats) -> (N, catégories)."""
    cols = []
    for cat in cats:
        if cat in ("fgp", "ftp"):
            made, att = totals[:, S[cat[:2] + "m"]], totals[:, S[cat[:2] + "a"]]
            cols.append(np.divide(made, att, out=np.zeros_like(made), where=att != 0))
        else:
            cols.append(totals[:, S[cat]])
    return np.stack(cols, axis=1)


def _expected_totals(p):
    """Stats d'un joueur s'il est aligné sur tous ses matchs attendus (avant plafonds)."""
    return p.per_game_st * p.games_st_expected + p.per_game_lt * p.games_lt_expected


def _combos(players, k):
    return list(itertools.combinations(players, k))


def _slack(team):
    """Matchs encore libres sous les plafonds par poste (lineup pas plein)."""
    return max(0.0, sum(float(v) for v in team.caps.values()) - sum(x.games_used for x in team.players))


def _fit(freed, wanted):
    """Part des matchs attendus des arrivants qui trouvent une place : matchs libérés / matchs voulus."""
    return np.where(wanted > 1e-9, np.minimum(1.0, freed / np.where(wanted > 1e-9, wanted, 1.0)), 0.0)


def _per_game_value(sim, x):
    return sim.value(x) / x.games_expected if x.games_expected > 1e-9 else 0.0


class _Side:
    """Effet d'un échange sur les stats de fin de saison d'une équipe, sans réoptimiser son lineup : les
    arrivants prennent les matchs libérés par les partants, la place libre sous les plafonds par poste, puis
    ceux des titulaires plus faibles qu'eux (valeur par match), dont les stats sont retirées en proportion.
    `rate` : part des matchs attendus réellement alignés (1 pour moi, taux d'alignement pour l'adversaire)."""

    def __init__(self, sim, team, outs, ins, rate=1.0):
        self.rate, self.slack = rate, _slack(team)
        pg = lambda x: _per_game_value(sim, x)
        self.out_used = np.array([sum(x.totals for x in c) for c in outs])
        self.out_games = np.array([sum(x.games_used for x in c) for c in outs])
        self.in_exp = np.array([sum(_expected_totals(x) for x in c) for c in ins]) * rate
        self.in_games = np.array([sum(x.games_expected for x in c) for c in ins]) * rate
        starters = [x for x in team.players if x.games_used > 1e-9]
        width = len(sim.teams[0].final)
        w = np.array([pg(x) for x in starters] or [np.inf])
        games = np.array([x.games_used for x in starters] or [0.0])
        totals = np.array([x.totals for x in starters] or [np.zeros(width)])
        self.level = np.array([sum(pg(x) * x.games_expected for x in c) / max(sum(x.games_expected for x in c), 1e-9)
                               for c in ins])
        weaker = (w[None, :] < self.level[:, None]).astype(float)
        self.disp_games, self.disp_totals = weaker @ games, weaker @ totals
        k = max((len(c) for c in outs), default=1)
        pad = lambda c, f, z: [f(x) for x in c] + [z] * (k - len(c))
        self.out_w = np.array([pad(c, pg, np.inf) for c in outs])
        self.out_g = np.array([pad(c, lambda x: x.games_used, 0.0) for c in outs])
        self.out_t = np.array([pad(c, lambda x: x.totals, np.zeros(width)) for c in outs])

    def delta(self, o, i):
        """Variation des stats pour les paires (partants o, arrivants i)."""
        overlap = self.out_w[o] < self.level[i][:, None]       # partants déjà comptés dans les matchs libérés
        movable = np.maximum(self.disp_games[i] - (overlap * self.out_g[o]).sum(axis=1), 0.0)
        movable_t = self.disp_totals[i] - (overlap[:, :, None] * self.out_t[o]).sum(axis=1)
        free = self.out_games[o] + self.slack
        take = np.clip(self.in_games[i] - free, 0.0, movable)
        share = np.where(movable > 1e-9, take / np.where(movable > 1e-9, movable, 1.0), 0.0)[:, None]
        fit = _fit(free + take, self.in_games[i])[:, None]
        return self.in_exp[i] * fit - self.out_used[o] - movable_t * share


def _screen(sim, me, partner, max_players, tolerance, batch=4000, offered=None):
    """Crible rapide des échanges k contre k avec `partner` : [(gain moi, gain partenaire, cédés, reçus)].
    offered : mes joueurs à céder (recherche autour d'eux : je cède un ou plusieurs d'entre eux, rien d'autre)."""
    mine, theirs = sim.teams[me], sim.teams[partner]
    rate = theirs.lineup_rate or 1.0
    my_players = list(offered) if offered else [x for x in mine.players if x.found]
    their_players = [x for x in theirs.players if x.found]
    base_totals = np.array([t.final for t in sim.teams])
    base_values = sim.base_values.astype(float)
    base_rows = sim.base_total[[me, partner]]
    out, tested = [], 0
    for k in range(1, max_players + 1):
        gives, recvs = _combos(my_players, k), _combos(their_players, k)
        if not gives or not recvs:
            continue
        vg = np.array([sum(sim.trade_value(x) for x in c) for c in gives])
        vr = np.array([sum(sim.trade_value(x) for x in c) for c in recvs])
        scale = np.maximum(np.abs(vg)[:, None], np.abs(vr)[None, :])
        diff = np.where(scale < 1e-9, 1.0, np.abs(vr[None, :] - vg[:, None]) / np.where(scale < 1e-9, 1, scale))
        gi, ri = np.nonzero(diff <= tolerance + 1e-9)
        tested += len(gives) * len(recvs)
        if not len(gi):
            continue
        # effet sur les stats des deux équipes (voir _Side) : un remplaçant cédé ne libère aucun match, un
        # arrivant ne joue que s'il trouve une place (matchs libérés, place libre, titulaire plus faible)
        my_side = _Side(sim, mine, gives, recvs)
        their_side = _Side(sim, theirs, recvs, gives, rate)
        for start in range(0, len(gi), batch):
            g, r = gi[start:start + batch], ri[start:start + batch]
            values = np.repeat(base_values[None], len(g), axis=0)
            values[:, me] = _values_batch(base_totals[me] + my_side.delta(g, r), sim.cats)
            values[:, partner] = _values_batch(base_totals[partner] + their_side.delta(r, g), sim.cats)
            gains = _rows_points(values, [me, partner], sim.cats, sim.weights) - base_rows
            for (a, b), gg, rr in zip(gains, g, r):
                out.append((float(a), float(b), list(gives[gg]), list(recvs[rr])))
    return out, tested


def _core(sim, me, move):
    """Ce qui compte pour moi dans un échange : partants qui jouaient, arrivants alignés (sans le
    remplissage, qui ne change rien à mon équipe)."""
    used = {x.name: x.games_used for x in sim.teams[me].players}
    give = frozenset(x.name for x in move.give if used.get(x.name, 0.0) >= 1.0)
    aligned = {name for name, games, *_ in move.recv_detail if games >= 1.0}
    recv = frozenset(x.name for x in move.recv if (x.display or x.name) in aligned)
    return move.partner_name, give, recv


def _interleave(groups):
    """Une file par taille d'échange (1c1, 2c2...) : les places à simuler sont réparties entre elles."""
    out, i = [], 0
    while any(i < len(g) for g in groups):
        out += [g[i] for g in groups if i < len(g)]
        i += 1
    return out


def _auto(sim, me, p, conf):
    partners = [sim.index(p["other"])] if p["other"] else [i for i in range(len(sim.teams)) if i != me]
    max_players = max(1, min(int(conf.get("auto_max_players", 2)), MAX_PLAYERS))
    tolerance = float(conf.get("value_tolerance", 0.10))
    shortlist = max(1, int(conf.get("auto_shortlist", 40)))
    min_partner = float(conf.get("auto_min_partner_gain", 0.0))
    limit = int(conf.get("max_results", 20))
    minimum = min(int(conf.get("min_results", 10)), limit)
    per_manager = max(1, int(conf.get("auto_per_manager", 5)))
    min_gain = min_move_gain(conf)
    offered = [sim.find_on(sim.teams[me], name) for name in p.get("players") or []]
    if len({id(x) for x in offered}) < len(offered):
        raise MovesError("Moves | Player : le même joueur est listé deux fois.")
    if offered:
        max_players = min(max_players, len(offered))
    screened, tested = [], 0
    for partner in partners:
        found, n = _screen(sim, me, partner, max_players, tolerance, offered=offered)
        tested += n
        screened += [(partner,) + c for c in found]
    # le crible est approché : marge d'un demi-point sur le gain du partenaire avant la simulation exacte
    pool = [c for c in screened if c[1] > 0 and c[2] >= min_partner - 0.5]
    pool.sort(key=lambda c: -(c[1] + 0.25 * min(c[2], 0.0)))
    queue = _interleave([[c for c in pool if len(c[3]) == k] for k in range(1, max_players + 1)])

    best, losing, simulated, rounds = {}, {}, 0, 0
    while queue and rounds < 4:
        # par lot de Auto shortlist ; au plus 2 x Auto per manager simulés par manager et par lot (variété)
        batch, count, rest = [], {}, []
        for c in queue:
            if len(batch) < shortlist and count.get(c[0], 0) < 2 * per_manager:
                count[c[0]] = count.get(c[0], 0) + 1
                batch.append(c)
            else:
                rest.append(c)
        queue, rounds = rest, rounds + 1
        for partner, gain, _, give, recv in batch:
            gone_me, gone_partner = {id(x) for x in give}, {id(x) for x in recv}
            my_entries = ([_entry(x) for x in sim.teams[me].players if id(x) not in gone_me]
                          + [_entry(x) for x in recv])
            partner_entries = ([_entry(x) for x in sim.teams[partner].players if id(x) not in gone_partner]
                               + [_entry(x) for x in give])
            teams, points = sim.evaluate(me, my_entries, partner, partner_entries)
            move = sim.move(me, give, recv, teams, points, partner)
            move.partner_name, move.screen_gain = sim.teams[partner].manager, gain
            simulated += 1
            if move.my_gain < min_gain:
                continue
            target = best if move.partner_gain >= min_partner - 1e-9 else losing
            key = _core(sim, me, move)
            kept = target.get(key)
            # même effet pour moi : on garde le plus équitable (le remplissage sert à équilibrer)
            if kept is None or (abs(move.trade_diff), -move.my_gain) < (abs(kept.trade_diff), -kept.my_gain):
                target[key] = move
        if len(best) >= minimum:
            break

    def balanced(moves, cap):
        moves = sorted(moves, key=display_order)
        shown, out = {}, []
        for m in moves:
            if shown.get(m.partner_name, 0) < per_manager:
                shown[m.partner_name] = shown.get(m.partner_name, 0) + 1
                out.append(m)
        return out[:cap]

    moves = balanced(best.values(), limit)
    # trop peu d'échanges gagnant-gagnant : complétés par ceux où je gagne mais où l'autre manager perd
    extra = [m for m in balanced(losing.values(), limit) if m.partner_gain < min_partner - 1e-9]
    for m in extra[:max(0, minimum - len(moves))]:
        m.partner_loses = True
        moves.append(m)
    log.info("[Moves auto] %d échanges possibles, %d à ±%.0f %% de valeur, %d intéressants au crible, "
             "%d simulés, %d gagnant-gagnant, %d affichés.", tested, len(screened), 100 * tolerance, len(pool),
             simulated, len(best), len(moves))
    return {"mode": "auto", "partner": None, "partner_team": None, "fixed": offered, "tested": tested,
            "kept": len(screened), "screened": len(pool), "simulated": simulated, "win_win": len(best),
            "tolerance": tolerance, "min_partner_gain": min_partner, "max_players": max_players,
            "partners": [sim.teams[i].manager for i in partners], "moves": moves}


MIN_RECV_GAMES = 0.5      # recherche auto de joueurs libres : matchs alignés minimum du joueur ajouté
TIE_GAIN = 0.10           # recherche auto de joueurs libres : gains plus proches = même gain (pts roto)
TIE_SCREEN_MARGIN = 0.5   # autres joueurs à lâcher simulés : à moins de 0,5 pt du meilleur au crible
TIE_EXTRA_DROPS = 3       # ... au plus 3 par joueur libre affiché


def _tie_break(found, min_gain=0.0):
    """Parmi les mouvements d'un même joueur libre (joueurs lâchés différents), le meilleur gain ; à gain
    presque égal (TIE_GAIN), on lâche le joueur de plus faible valeur (celui qui sert le moins en remplaçant).
    Les autres joueurs à lâcher pour le même gain sont notés dans `alternatives`."""
    top = max(m.my_gain for m in found)
    ties = sorted((m for m in found if m.my_gain >= max(top - TIE_GAIN - 1e-9, min_gain)),
                  key=lambda m: (m.value_give, -m.my_gain))
    chosen = ties[0]
    chosen.alternatives = [m.give for m in ties[1:]]
    return chosen


def _auto_free_agents(sim, me, conf):
    """Recherche auto de joueurs libres : (mon joueur lâché, joueur libre) au crible, puis simulation exacte."""
    my_team, ctx = sim.teams[me], sim.ctx
    drops = [x for x in my_team.players if str(x.slot or "").upper() not in IL_SLOTS]
    if not drops:
        raise MovesError("Aucun joueur à lâcher (hors postes IL) dans mon effectif.")
    # les joueurs libres projetés comme mes joueurs (matchs attendus, stats st / lt), sans plafond
    roster = {"players": [_fa_entry(pl) for pl in _free_pool(sim)],
              "games": {b: {"remaining": 1e9} for b in BUCKETS}}
    adds = [x for x in project_team("libres", "libres", roster, None, ctx["lt_pool"], ctx["st_pool"], ctx["games"],
                                    ctx["today"], ctx["horizon"], ctx["league"]).players
            if x.found and x.games_expected > 1e-9]
    if not adds:
        raise MovesError("Aucun joueur libre avec des matchs attendus d'ici la fin de saison.")

    # crible : effet sur mes stats de fin de saison sans réoptimiser le lineup (voir _Side)
    side = _Side(sim, my_team, [[d] for d in drops], [[a] for a in adds])
    g, r = (a.ravel() for a in np.meshgrid(np.arange(len(drops)), np.arange(len(adds)), indexing="ij"))
    values = np.repeat(sim.base_values.astype(float)[None], len(g), axis=0)
    values[:, me] = _values_batch(np.array(my_team.final) + side.delta(g, r), sim.cats)
    gains = _rows_points(values, [me], sim.cats, sim.weights)[:, 0] - sim.base_total[me]

    limit = int(conf.get("fa_results", 6))
    shortlist = max(int(conf.get("fa_shortlist", 15)), limit)
    min_gain = min_move_gain(conf)
    per_add = {}
    for i in np.argsort(-gains):            # 2 joueurs à lâcher au plus par joueur libre (variété)
        if len(per_add.setdefault(r[i], [])) < 2:
            per_add[r[i]].append(i)
    candidates = sorted((i for picks in per_add.values() for i in picks), key=lambda i: -gains[i])[:shortlist]

    simulated = {}                          # indice du crible -> mouvement simulé exactement

    def run(i):
        if i not in simulated:
            drop, add = drops[g[i]], adds[r[i]]
            teams, points = sim.evaluate(me, [_entry(x) for x in my_team.players if x is not drop] + [_entry(add)])
            move = sim.move(me, [drop], [x for x in teams[me].players if x.name == add.name], teams, points)
            move.screen_gain = float(gains[i])
            simulated[i] = move
        return simulated[i]

    def kept(add_index):
        # un joueur libre que mon lineup n'alignerait jamais ne peut pas me faire gagner : un gain affiché
        # viendrait seulement d'un autre lineup pour mes joueurs (voir optimize_team), pas de lui
        return [m for i, m in simulated.items() if r[i] == add_index and m.recv_games >= MIN_RECV_GAMES]

    def best_of(add_index):
        found = kept(add_index)
        return max(found, key=lambda m: m.my_gain) if found else None

    for i in candidates:
        run(i)
    adds_kept = [a for a in dict.fromkeys(r[i] for i in candidates)
                 if best_of(a) is not None and best_of(a).my_gain >= min_gain]
    adds_kept.sort(key=lambda a: (-round(best_of(a).my_gain, 3), -best_of(a).value_recv))
    moves = []
    for a in adds_kept[:limit]:
        # autres joueurs à lâcher presque aussi bons au crible : simulés aussi, pour départager les égalités
        top = max(gains[i] for i in simulated if r[i] == a)
        others = [i for i in np.argsort(-gains) if r[i] == a and i not in simulated
                  and gains[i] >= top - TIE_SCREEN_MARGIN][:TIE_EXTRA_DROPS]
        for i in others:
            run(i)
        moves.append(_tie_break(kept(a), min_gain))
    moves.sort(key=lambda m: (-round(m.my_gain, 3), -m.value_recv))
    best = adds_kept
    log.info("[Moves auto joueurs libres] %d de mes joueurs x %d joueurs libres = %d combinaisons au crible, "
             "%d simulées, %d qui me font gagner au moins %.2f pt, %d affichées.", len(drops), len(adds), len(g),
             len(simulated), len(best), min_gain, len(moves))
    return {"mode": AUTO_FREE_AGENT, "partner": None, "partner_team": None, "fixed": [], "tested": len(g),
            "kept": len(best), "min_gain": min_gain, "simulated": len(simulated), "drops": len(drops), "free_count": len(adds),
            "tolerance": None, "moves": moves}


# --- Restitution -----------------------------------------------------------------------------

def _names(players):
    return ", ".join(x.display or x.name for x in players)


def _games(detail):
    """« Austin Reaves 72/74 (st 10 + lt 62), Yaxel Lendeborg 0/70 (st 0 + lt 0) » : matchs alignés / attendus
    d'ici la fin, dont horizon court (st) et reste de la saison (lt)."""
    return ", ".join(f"{name} {used:.0f}/{expected:.0f} (st {st:.0f} + lt {lt:.0f})"
                     for name, used, expected, st, lt in detail)


PCT_CATS = {"fgp", "ftp"}


def _stat(cat, value):
    """Écart de stat affiché : FG% / FT% en points de pourcentage (+0,35), le reste en unités (+120,5)."""
    return round(100 * float(value), 2) if cat in PCT_CATS else round(float(value), 1)


def _changes(changes):
    return ", ".join(f"{name} {delta:+.0f}" for name, delta in changes) or "aucun"


def _given(m):
    """Joueurs lâchés, avec les autres choix possibles pour le même gain (recherche auto de joueurs libres)."""
    text = _names(m.give)
    if m.alternatives:
        text += f" (ou {' / '.join(_names(a) for a in m.alternatives)}, même gain)"
    return text


def _pct(value):
    return "" if value is None else round(100 * value, 1)


HEADER_ROW = 9    # moves_reco : en-tête du tableau toujours en ligne 9, propositions à partir de la ligne 10
FIXED_COLUMNS = ("#", "Je cède", "Je reçois", "Valeur cédée", "Valeur reçue", "Écart valeur (%)",
                 "Gain roto (moi)", "Gain roto (partenaire)", "Manager", "Statut", "Matchs alignés (reçus)",
                 "Effet sur mes autres joueurs", "Points projetés", "Rang projeté", "Titre (%)", "Podium (%)")


def _move_row(i, m, manager, status, cats):
    """Une ligne de mouvement, dans les colonnes FIXED_COLUMNS puis points et stats par catégorie."""
    row = [i, _given(m), _names(m.recv), round(m.value_give, 1), round(m.value_recv, 1), _pct(m.value_diff),
           round(m.my_gain, 2), round(m.partner_gain, 2) if m.partner_gain is not None else "",
           manager, status, _games(m.recv_detail), _changes(m.lineup_changes),
           round(m.my_points, 1), m.rank, _pct(m.p_first), _pct(m.p_podium)]
    row += [round(float(d), 2) for d in m.cat_delta]
    row += [_stat(c, d) for c, d in zip(cats, m.stat_delta)] if m.stat_delta is not None else [""] * len(cats)
    if m.perceived_give is not None:
        row += [round(m.perceived_give, 1), round(m.perceived_recv, 1), _pct(_value_diff(m.perceived_give, m.perceived_recv))]
    else:
        row += ["", "", ""]
    return row


def _header(labels):
    """En-tête des mouvements : colonnes fixes, points puis stats par catégorie, valeurs perçues."""
    return list(FIXED_COLUMNS) + [f"{l} (pts)" for l in labels] + [f"{l} (stats)" for l in labels] + \
        list(PERCEIVED_COLUMNS)


def rows(out):
    """Lignes de l'onglet moves_reco."""
    from scripts.draft.pool import CATEGORY_LABELS

    p, cats = out["plan"], out["categories"]
    labels = [CATEGORY_LABELS.get(c, c) for c in cats]
    auto = out["mode"] == "auto"
    trade = out["mode"] in ("manager", "auto")
    partner = out["partner_team"]
    lines = [[f"Mouvements - simulation au {out['today'].isoformat()}"]]
    if auto:
        lines.append(["Mouvement", "recherche auto d'échanges avec " + ", ".join(out["partners"])])
        if out["fixed"]:
            lines.append(["Joueurs proposés", "Je cède un ou plusieurs de : " + _names(out["fixed"])])
        lines.append(["Taille", f"1 contre 1 à {out['max_players']} contre {out['max_players']}"])
        lines.append(["Tolérance de valeur", f"±{100 * out['tolerance']:.0f} %"])
        lines.append(["Gain minimum du partenaire", f"{out['min_partner_gain']:+.2f} pts roto"])
        lines.append(["Combinaisons", f"{out['tested']} testées, {out['kept']} à valeur équivalente, "
                                      f"{out['screened']} intéressantes au crible, {out['simulated']} simulées, "
                                      f"{out['win_win']} gagnant-gagnant, {len(out['moves'])} affichées"])
    elif trade:
        lines.append(["Mouvement", f"échange avec {partner.manager} ({partner.team_name})"])
        lines.append(["Joueurs fixés", ("Je cède : " if p["mine"] else "Je reçois : ") + _names(out["fixed"])])
        lines.append(["Tolérance de valeur", f"±{100 * out['tolerance']:.0f} %"])
        lines.append(["Combinaisons", f"{out['tested']} testées, {out['kept']} à valeur équivalente, "
                                      f"{len(out['moves'])} affichées"])
    elif out["mode"] == AUTO_FREE_AGENT:
        lines.append(["Mouvement", "recherche auto de joueurs libres (statut waivers non vérifié)"])
        lines.append(["Combinaisons", f"{out['drops']} de mes joueurs (hors IL) x {out['free_count']} joueurs "
                                      f"libres = {out['tested']} testées au crible, {out['simulated']} simulées, "
                                      f"{out['kept']} qui me font gagner au moins {out['min_gain']:.2f} pt, "
                                      f"{len(out['moves'])} affichées"])
    else:
        lines.append(["Mouvement", "joueurs libres (statut waivers non vérifié)"])
        lines.append(["Joueurs lâchés", _names(out["fixed"])])
        lines.append(["Joueurs libres testés", f"{out.get('free_count', 0)} (les meilleurs en valeur)"])
    base = f"{out['base_points']:.1f} pts projetés, rang {out['base_rank']}"
    if out.get("base_first") is not None:
        base += f", titre {100 * out['base_first']:.1f} %, podium {100 * out['base_podium']:.1f} %"
    lines.append(["Sans mouvement", base])
    # bloc d'informations de hauteur fixe : l'en-tête du tableau est toujours sur la même ligne
    lines += [[] for _ in range(HEADER_ROW - 1 - len(lines))]
    lines.append(_header(labels))
    if not out["moves"] and auto:
        lines.append(["", "aucun échange gagnant-gagnant : élargissez la tolérance (Optim | Moves | Value "
                          "tolerance), Auto max players ou Auto shortlist, ou baissez Auto min partner gain"])
    elif not out["moves"] and out["mode"] == AUTO_FREE_AGENT:
        lines.append(["", "aucun joueur libre ne me fait gagner au moins Optim | Moves | Min gain : effectif "
                          "meilleur que les joueurs libres testés (ou élargissez Free agent shortlist)"])
    elif not out["moves"]:
        lines.append(["", "aucune combinaison à valeur équivalente : élargissez la tolérance "
                          "(Optim | Moves | Value tolerance)" if trade else "aucun joueur libre"])
    for i, m in enumerate(out["moves"], 1):
        if auto:
            manager, status = m.partner_name, "il perd des points" if m.partner_loses else "gagnant-gagnant"
        elif trade:
            manager, status = partner.manager, "hors tolérance" if m.out_of_tolerance else "dans la tolérance"
        else:
            manager, status = "joueurs libres", ""
        lines.append(_move_row(i, m, manager, status, cats))
    lines.append([])
    lines.append(["Valeur perçue (3 dernières colonnes) = moyenne réelle de la saison par match au-dessus du niveau de "
                  "remplacement x matchs à venir (blessure actuelle comprise, matchs déjà manqués non comptés) : ce que "
                  "voient les managers ; l'équilibre des échanges (Value tolerance) se juge sur elle (Optim | Moves | "
                  "Value basis), mon gain toujours sur les projections."])
    lines.append(["Valeur = valeur par match au-dessus du niveau de remplacement (dernier joueur d'effectif "
                  "de la ligue, st puis lt) x matchs attendus d'ici la fin de saison, jamais négative. "
                  "Matchs alignés (reçus) = matchs que mon lineup optimisé donnerait à chaque joueur reçu, sur ses "
                  "matchs attendus d'ici la fin de saison (un joueur peu utile reste sur le banc) ; effet sur mes "
                  "autres joueurs = matchs alignés en plus ou en moins. Échanges triés du meilleur gain roto pour "
                  "moi au moins bon, puis du plus équitable au moins équitable ; « hors tolérance » = ajouté pour atteindre le nombre minimum de propositions. "
                  "Recherche auto : échanges où je gagne et où l'autre manager ne perd rien (« gagnant-gagnant »), "
                  "complétés si besoin par ceux où il perd des points ; un même effet sur mon équipe n'est montré "
                  "qu'une fois (version la plus équitable, le joueur en plus servant à équilibrer la valeur). "
                  "Gain roto = points roto attendus en fin de saison, après réoptimisation de mon lineup ; "
                  "colonnes (pts) = points roto gagnés ou perdus par catégorie ; colonnes (stats) = écart de mes stats de fin de "
                  "saison (FG% / FT% en points de pourcentage, ex. 0,35 = +0,35 %, les autres en unités sur la saison)."])
    return lines


def console(out):
    p = out["plan"]
    if out["mode"] == "auto":
        text = [f"Recherche auto d'échanges ({out['tested']} combinaisons, {out['kept']} à "
                f"±{100 * out['tolerance']:.0f} % de valeur, {out['simulated']} simulées, "
                f"{out['win_win']} gagnant-gagnant)",
                f"Sans mouvement : {out['base_points']:.1f} pts, rang {out['base_rank']}"]
        for i, m in enumerate(out["moves"][:10], 1):
            text.append(f"{i:>2}. avec {m.partner_name} : {_names(m.give)} -> {_names(m.recv)} : {m.my_gain:+.2f} pts, "
                        f"{m.partner_name} {m.partner_gain:+.2f} (valeur {m.value_give:.1f} -> {m.value_recv:.1f}, "
                        f"{100 * m.value_diff:+.1f} %), rang {m.rank}" + (" [il perd des points]" if m.partner_loses else ""))
        if not out["moves"]:
            text.append("Aucun échange gagnant-gagnant trouvé.")
        return "\n".join(text)
    if out["mode"] == AUTO_FREE_AGENT:
        text = [f"Recherche auto de joueurs libres ({out['drops']} de mes joueurs x {out['free_count']} joueurs "
                f"libres, {out['simulated']} simulées, {out['kept']} qui me font gagner)",
                f"Sans mouvement : {out['base_points']:.1f} pts, rang {out['base_rank']}"]
        for i, m in enumerate(out["moves"], 1):
            text.append(f"{i:>2}. lâcher {_given(m)}, prendre {_names(m.recv)} : {m.my_gain:+.2f} pts, "
                        f"rang {m.rank} ; matchs alignés : {_games(m.recv_detail)}")
        if not out["moves"]:
            text.append("Aucun joueur libre ne me fait gagner de points.")
        return "\n".join(text)
    head = (f"Échange avec {out['partner_team'].manager}" if out["mode"] == "manager" else "Joueurs libres")
    text = [f"{head} - {'je cède' if p['mine'] else 'je reçois'} : {_names(out['fixed'])}",
            f"Sans mouvement : {out['base_points']:.1f} pts, rang {out['base_rank']}"]
    if out["mode"] == "manager":
        text.append(f"{out['tested']} combinaisons, {out['kept']} à ±{100 * out['tolerance']:.0f} % de valeur")
    for i, m in enumerate(out["moves"][:10], 1):
        partner = f", {out['partner_team'].manager} {m.partner_gain:+.2f}" if m.partner_gain is not None else ""
        flag = " [hors tolérance]" if m.out_of_tolerance else ""
        text.append(f"{i:>2}. {_names(m.give)} -> {_names(m.recv)} : {m.my_gain:+.2f} pts{partner} "
                    f"(valeur {m.value_give:.1f} -> {m.value_recv:.1f}), rang {m.rank}{flag}\n"
                    f"    matchs alignés : {_games(m.recv_detail)} ; mes autres joueurs : {_changes(m.lineup_changes)}")
    return "\n".join(text)


# --- Simuler un mouvement (Moves | Team 1 / Team 2) ---------------------------------------------

SIM_ROW = 40     # moves_reco : bloc de la simulation à partir de cette ligne (les recherches s'arrêtent avant)


def _locate(sim, name):
    """(indice de l'équipe, joueur) ; joueur libre : (None, joueur des projections)."""
    key = name_key(name)
    target = sim.ctx["lt_pool"].find(name)
    target_key = name_key(target.name) if target is not None else None
    for i, team in enumerate(sim.teams):
        for p in team.players:
            keys = _keys(p)
            if key in keys or (target_key and target_key in keys):
                return i, p
    if target is None:
        raise MovesError(f"« {name} » introuvable : ni dans un effectif, ni dans les projections.")
    return None, target


def _side(sim, names, label):
    found = [_locate(sim, n) for n in names]
    owners = {i for i, _ in found}
    if len(owners) > 1:
        who = ", ".join(f"{n} ({sim.teams[i].manager if i is not None else 'joueur libre'})"
                        for n, (i, _) in zip(names, found))
        raise MovesError(f"Moves | {label} : les joueurs doivent appartenir au même manager (ou être tous libres) : "
                         f"{who}.")
    players = [p for _, p in found]
    if len({id(p) for p in players}) < len(players):
        raise MovesError(f"Moves | {label} : le même joueur est listé deux fois.")
    return owners.pop(), players


def simulate_trade(result, league):
    """Échange (ou mouvement avec des joueurs libres) saisi dans Moves | Team 1 / Team 2, entre deux
    managers quelconques. Mon lineup est réoptimisé si mon équipe est concernée ; une autre équipe garde son
    lineup standard (taux d'alignement). Gains et rang du point de vue de Team 1, gain de Team 2 à côté."""
    sim = Simulator(result, league)
    conf = (league.get("moves") or {}).get("simulate") or {}
    names1 = [str(n).strip() for n in conf.get("team1") or [] if str(n).strip()]
    names2 = [str(n).strip() for n in conf.get("team2") or [] if str(n).strip()]
    if not names1 or not names2:
        raise MovesError("Simuler un mouvement : renseignez au moins un joueur dans Moves | Team 1 et dans "
                         "Moves | Team 2 (onglet settings).")
    i1, give = _side(sim, names1, "Team 1")
    i2, recv = _side(sim, names2, "Team 2")
    if i1 is None:
        raise MovesError("Moves | Team 1 : joueurs libres ; mettez les joueurs libres en Team 2 et les joueurs "
                         "lâchés en Team 1.")
    if i1 == i2:
        raise MovesError(f"Moves | Team 1 et Team 2 : tous les joueurs sont chez {sim.teams[i1].manager}.")
    me = sim.index(league.get("draft", {}).get("my_team"))
    team1 = sim.teams[i1]
    gone1 = {id(p) for p in give}
    entries1 = [_entry(x) for x in team1.players if id(x) not in gone1]
    entries1 += [_entry(x) for x in recv] if i2 is not None else [_fa_entry(x) for x in recv]
    changed = {i1: entries1}
    if i2 is not None:
        gone2 = {id(p) for p in recv}
        changed[i2] = [_entry(x) for x in sim.teams[i2].players if id(x) not in gone2] + [_entry(x) for x in give]
    teams = list(sim.teams)
    for i, entries in changed.items():
        teams[i] = sim.rebuild(sim.teams[i], entries)
        if i != me:
            apply_lineup_rate(teams[i], sim.teams[i].lineup_rate)
    if me in changed:
        optimize_team(teams[me], [t for k, t in enumerate(teams) if k != me], sim.cats, sim.weights)
    values = np.array([category_values(t.final, sim.cats) for t in teams])
    points = roto_points(values, sim.cats, sim.weights)
    received = {p.name for p in recv}
    move = sim.move(i1, give, [x for x in teams[i1].players if x.name in received], teams, points, i2)
    sims = int(league.get("season", {}).get("simulations", 2000))
    proj = standings_projection(teams, sim.cats, sim.weights, sims=sims)
    move.p_first, move.p_podium = float(proj["p_first"][i1]), float(proj["p_podium"][i1])
    total = points.sum(axis=1)

    def rank(i):
        return 1 + int(sum(1 for k, v in enumerate(total) if k != i and v > total[i] + 1e-9))

    side2 = {"manager": sim.teams[i2].manager, "before": float(sim.base_total[i2]), "after": float(total[i2]),
             "rank_before": sim.rank_of(i2), "rank_after": rank(i2)} if i2 is not None else None
    log.info("[Moves simulation] %s cède %s, reçoit %s%s : %+.2f pts%s.", team1.manager, _names(move.give),
             _names(move.recv), "" if i2 is not None else " (joueurs libres)", move.my_gain,
             f", {side2['manager']} {move.partner_gain:+.2f}" if side2 else "")
    return {"mode": "simulate", "today": sim.ctx["today"], "categories": sim.cats, "move": move,
            "team1": {"manager": team1.manager, "before": float(sim.base_total[i1]), "after": float(total[i1]),
                      "rank_before": sim.rank_of(i1), "rank_after": move.rank, "is_me": i1 == me},
            "team2": side2, "free_agents": i2 is None}


def _status(m, out):
    if out["free_agents"]:
        return ""
    t1, t2 = out["team1"]["manager"], out["team2"]["manager"]
    if m.my_gain >= 0 and m.partner_gain >= 0:
        return "gagnant-gagnant"
    if m.my_gain >= 0:
        return f"{t2} perd des points"
    if m.partner_gain >= 0:
        return f"{t1} perd des points"
    return "les deux perdent des points"


def simulation_rows(out):
    """Bloc de l'onglet moves_reco à partir de la ligne SIM_ROW : même disposition que les recherches
    (8 lignes d'informations, en-tête, mouvement)."""
    from scripts.draft.pool import CATEGORY_LABELS

    m, cats, t1, t2 = out["move"], out["categories"], out["team1"], out["team2"]
    labels = [CATEGORY_LABELS.get(c, c) for c in cats]
    other = t2["manager"] if t2 else "joueurs libres"
    lines = [[f"Simulation d'un mouvement - au {out['today'].isoformat()}"],
             ["Mouvement", f"{t1['manager']} cède {_names(m.give)} ; reçoit {_names(m.recv)} "
                           f"({'de ' + other if t2 else 'joueurs libres, statut waivers non vérifié'})"],
             ["Point de vue", f"colonnes « moi » = {t1['manager']}" + (f", « partenaire » = {other}" if t2 else "")
              + ("" if t1["is_me"] else " (lineup standard : mon lineup n'est optimisé que pour mon équipe)")]]
    for label, side in (("Team 1", t1), ("Team 2", t2)):
        if side:
            lines.append([f"{label} : {side['manager']}", f"{side['before']:.1f} pts (rang {side['rank_before']}) -> "
                                                         f"{side['after']:.1f} pts (rang {side['rank_after']})"])
    lines += [[] for _ in range(HEADER_ROW - 1 - len(lines))]
    lines.append(_header(labels))
    lines.append(_move_row(1, m, other, _status(m, out), cats))
    return lines


def simulation_console(out):
    m, t1, t2 = out["move"], out["team1"], out["team2"]
    text = [f"Simulation : {t1['manager']} cède {_names(m.give)}, reçoit {_names(m.recv)}"
            + (f" (de {t2['manager']})" if t2 else " (joueurs libres)"),
            f"{t1['manager']} : {t1['before']:.1f} -> {t1['after']:.1f} pts ({m.my_gain:+.2f}), "
            f"rang {t1['rank_before']} -> {t1['rank_after']}"]
    if t2:
        text.append(f"{t2['manager']} : {t2['before']:.1f} -> {t2['after']:.1f} pts ({m.partner_gain:+.2f}), "
                    f"rang {t2['rank_before']} -> {t2['rank_after']}")
    text.append(f"Matchs alignés (reçus) : {_games(m.recv_detail)} ; autres joueurs : {_changes(m.lineup_changes)}")
    return "\n".join(text)
