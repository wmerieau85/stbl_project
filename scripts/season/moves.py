"""Simulation de mouvements : échanges avec un manager ou joueurs libres (onglet moves_reco).

Réglages de l'onglet settings :
- Moves | Player 1..4 : manager | joueur. Le manager est soit le mien (je cède ces joueurs), soit celui
  de Moves | Compare (je reçois ces joueurs). Ligne sans joueur : ignorée.
- Moves | Compare : « manager » + nom du manager (échange), ou « free agent » (je lâche mes joueurs).
- Optim | Moves : tolérance de valeur, nombre de résultats...

Échange : autant de joueurs reçus que cédés ; toutes les combinaisons de l'effectif d'en face (ou du
mien) sont testées, celles dont la valeur s'écarte de plus de la tolérance sont écartées, les autres
classées par gain de points roto attendus pour moi (le gain de l'autre manager est indiqué).
Joueurs libres : chaque joueur libre est testé seul à la place des joueurs lâchés, puis (plusieurs
joueurs lâchés) les meilleurs sont combinés.

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

from scripts.draft.engine import category_values, roto_points
from scripts.names import name_key
from scripts.season.engine import apply_lineup_rate, optimize_team, project_team, standings_projection

log = logging.getLogger("stbl.season.moves")

MAX_PLAYERS = 4
FA_SCREEN = 150          # joueurs libres testés un par un (les meilleurs en valeur totale)
MAX_COMBINATIONS = 20000


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
    p_first: float | None = None
    p_podium: float | None = None

    @property
    def value_diff(self):
        return _value_diff(self.value_give, self.value_recv)


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

    def value(self, p):
        return player_value(p, self.repl_lt, self.repl_st)

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
        return Move(give, recv, sum(self.value(p) for p in give), sum(self.value(p) for p in recv),
                    float(total[me]), float(total[me] - self.base_total[me]), partner_gain, rank,
                    points[me] - self.base_points[me], teams, recv_games=sum(d[1] for d in detail),
                    recv_detail=detail, lineup_changes=changes,
                    stat_delta=category_values(teams[me].final, self.cats) - self.base_values[me])

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


def simulate(result, league):
    """Mouvements classés : {mode, plan, moves, tested, kept, base...}."""
    p = plan(league)
    sim = Simulator(result, league)
    conf = league.get("moves") or {}
    me = sim.index(p["me"])
    if p["kind"] == "manager":
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
        vg, vr = sum(sim.value(x) for x in give), sum(sim.value(x) for x in recv)
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
    moves.sort(key=lambda m: (abs(m.value_diff), -m.my_gain))   # du plus équitable au moins équitable
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
    taken = set().union(*(_keys(x) for t in sim.teams for x in t.players))
    pool = sim.ctx["lt_pool"]
    free = [pl for pl in pool.players if name_key(pl.name) not in taken and (pl.gp or 0) > 0]
    free.sort(key=lambda pl: -(pl.value_tot or 0.0))
    free = free[:FA_SCREEN]
    if not free:
        raise MovesError("Aucun joueur libre avec des projections.")
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


def _pct(value):
    return "" if value is None else round(100 * value, 1)


def rows(out):
    """Lignes de l'onglet moves_reco."""
    from scripts.draft.pool import CATEGORY_LABELS

    p, cats = out["plan"], out["categories"]
    labels = [CATEGORY_LABELS.get(c, c) for c in cats]
    trade = out["mode"] == "manager"
    partner = out["partner_team"]
    lines = [[f"Mouvements - simulation au {out['today'].isoformat()}"]]
    if trade:
        lines.append(["Mouvement", f"échange avec {partner.manager} ({partner.team_name})"])
        lines.append(["Joueurs fixés", ("Je cède : " if p["mine"] else "Je reçois : ") + _names(out["fixed"])])
        lines.append(["Tolérance de valeur", f"±{100 * out['tolerance']:.0f} %"])
        lines.append(["Combinaisons", f"{out['tested']} testées, {out['kept']} à valeur équivalente, "
                                      f"{len(out['moves'])} affichées"])
    else:
        lines.append(["Mouvement", "joueurs libres (statut waivers non vérifié)"])
        lines.append(["Joueurs lâchés", _names(out["fixed"])])
        lines.append(["Joueurs libres testés", f"{out.get('free_count', 0)} (les meilleurs en valeur)"])
    base = f"{out['base_points']:.1f} pts projetés, rang {out['base_rank']}"
    if out.get("base_first") is not None:
        base += f", titre {100 * out['base_first']:.1f} %, podium {100 * out['base_podium']:.1f} %"
    lines.append(["Sans mouvement", base])
    lines.append([])
    header = ["#", "Je cède" if trade else "Je lâche", "Je reçois" if trade else "J'ajoute",
              "Valeur cédée", "Valeur reçue", "Écart valeur (%)"]
    if trade:
        header.append("Tolérance")
    header += ["Matchs alignés (reçus)", "Effet sur mes autres joueurs", "Gain roto (moi)"]
    if trade:
        header.append(f"Gain roto ({partner.manager})")
    header += ["Points projetés", "Rang projeté", "Titre (%)", "Podium (%)"] + [f"{l} (pts)" for l in labels]
    header += [f"{l} (stats)" for l in labels]
    lines.append(header)
    if not out["moves"]:
        lines.append(["", "aucune combinaison à valeur équivalente : élargissez la tolérance "
                          "(Optim | Moves | Value tolerance)" if trade else "aucun joueur libre"])
    for i, m in enumerate(out["moves"], 1):
        row = [i, _names(m.give), _names(m.recv), round(m.value_give, 1), round(m.value_recv, 1), _pct(m.value_diff)]
        if trade:
            row.append("hors tolérance" if m.out_of_tolerance else "oui")
        row += [_games(m.recv_detail), _changes(m.lineup_changes), round(m.my_gain, 2)]
        if trade:
            row.append(round(m.partner_gain, 2))
        row += [round(m.my_points, 1), m.rank, _pct(m.p_first), _pct(m.p_podium)]
        row += [round(float(d), 2) for d in m.cat_delta]
        row += [_stat(c, d) for c, d in zip(cats, m.stat_delta)] if m.stat_delta is not None else []
        lines.append(row)
    lines.append([])
    lines.append(["Valeur = valeur par match au-dessus du niveau de remplacement (dernier joueur d'effectif "
                  "de la ligue, st puis lt) x matchs attendus d'ici la fin de saison, jamais négative. "
                  "Matchs alignés (reçus) = matchs que mon lineup optimisé donnerait à chaque joueur reçu, sur ses "
                  "matchs attendus d'ici la fin de saison (un joueur peu utile reste sur le banc) ; effet sur mes "
                  "autres joueurs = matchs alignés en plus ou en moins. Échanges triés du plus équitable au moins "
                  "équitable ; « hors tolérance » = ajouté pour atteindre le nombre minimum de propositions. "
                  "Gain roto = points roto attendus en fin de saison, après réoptimisation de mon lineup ; "
                  "colonnes (pts) = points roto gagnés ou perdus par catégorie ; colonnes (stats) = écart de mes stats de fin de "
                  "saison (FG% / FT% en points de pourcentage, ex. 0,35 = +0,35 %, les autres en unités sur la saison)."])
    return lines


def console(out):
    p = out["plan"]
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
