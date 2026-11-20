"""Analyse des écarts entre la draft et la saison à date (onglet season_analysis).

Pour chaque équipe, on part des totaux de draft sur la saison (projections de la phase draft x matchs
prévus par la rotation de draft : colonne « GP draft » de l'onglet bdd, comme le bloc draft de board ; à
défaut, rotation de l'assistant de draft, les meilleurs joueurs par match jusqu'au plafond de la ligue), en
points roto exacts (rang dans chaque catégorie, totaux arrondis, comme Yahoo et board), et l'on arrive aux
standings Yahoo à date, par étapes :

    draft (saison, points exacts)
    + calendrier  : totaux ramenés à date (matchs déjà joués par l'équipe NBA de chaque joueur)
    = attendu à date
    + blessures / matchs : matchs joués par rapport aux matchs prévus à la draft
    + régime      : production réelle par match par rapport à la projection de draft (matchs comptés)
    + rotation    : matchs comptés pour l'équipe par rapport à ceux que la rotation de draft prévoyait
    + mouvements  : apport des joueurs arrivés, moins les matchs perdus des joueurs draftés partis
    + reste       : ce qui n'est attribué à aucun axe (joueurs draftés sans projection...)
    = réel (standings Yahoo, recalculés depuis le Team Log)

Les étapes sont enchaînées dans cet ordre (en roto les effets se chevauchent : l'ordre compte un peu) et
s'additionnent exactement. Le détail par joueur donne, pour chaque axe, les points que l'équipe perdrait si
l'on retirait cet effet du joueur (les effets des joueurs ne s'additionnent pas exactement).

Données : phases draft et season (stats réelles) de la base ; onglet yahoo (blocs draft, rosters et Team Log) ;
colonne GP draft de l'onglet bdd.
Sans dates de transactions, un joueur drafté parti voit ses matchs non comptés classés en mouvements, un
joueur toujours là en rotation.
"""

import logging
from collections import defaultdict

import numpy as np

from scripts.draft.engine import LOWER_IS_BETTER, category_values
from scripts.draft.pool import STATS
from scripts.names import name_key

log = logging.getLogger(__name__)

STEPS = (("injury", "Blessures / matchs"), ("perf", "Régime"), ("rotation", "Rotation"), ("moves", "Mouvements"))
SUMMARY_HEADER = ["Manager", "Draft (saison)", "Calendrier", "Attendu à date"] + \
    [label for _, label in STEPS] + ["Reste", "Réel (Yahoo)"]
DETAIL_HEADER = ["Manager", "Joueur", "Statut", "Matchs comptés prévus à date", "Matchs joués (NBA)",
                 "Matchs comptés", "Blessures / matchs (pts)", "Régime (pts)", "Rotation (pts)",
                 "Mouvements (pts)", "Total (pts)"]


def exact_points(values, categories, weights):
    """Points roto du classement réel : rang dans chaque catégorie (égalités partagées), comme Yahoo."""
    n = len(values)
    points = np.zeros(n)
    for j, cat in enumerate(categories):
        col = -values[:, j] if cat in LOWER_IS_BETTER else values[:, j]
        for i in range(n):
            points[i] += weights[j] * (1 + np.sum(col[i] > col) + 0.5 * (np.sum(col[i] == col) - 1))
    return points


def draft_rotation(players, cap):
    """{nom: matchs comptés sur la saison} : rotation de l'assistant de draft (engine.team_totals)."""
    left, alloc = float(cap), {}
    for p in sorted(players, key=lambda x: x.value_avg, reverse=True):
        games = max(0.0, min(float(p.gp or 0), left))
        alloc[p.name] = games
        left -= games
    return alloc


def analyze(draft_pool, real_pool, picks, rosters, team_log, cap, categories, weights, draft_games=None):
    """picks / rosters : {manager: [joueurs]} ; team_log : {manager: {joueur: (matchs, totaux STATS)}} ;
    draft_games : {manager: {joueur: matchs prévus à la draft}} (colonne GP draft de bdd), sinon rotation de
    l'assistant de draft.

    Retourne {"managers", "summary": {manager: {étape: points}}, "detail": [lignes joueur]}."""
    zero = np.zeros(len(STATS))
    team_games = defaultdict(float)
    for p in real_pool.players:
        team_games[p.team] = max(team_games[p.team], float(p.gp or 0))
    league_games = np.mean([g for g in team_games.values() if g]) if team_games else 0.0
    managers = sorted(team_log)
    full, axes, players_of = {}, {}, {}
    for m in managers:
        if draft_games and draft_games.get(m):
            alloc, drafted = {}, []
            for n, g in draft_games[m].items():
                p = draft_pool.find(n)
                if p is not None and p.gp:
                    if p.name not in alloc:
                        drafted.append(p)
                    alloc[p.name] = alloc.get(p.name, 0.0) + float(g or 0)
        else:
            drafted = [p for p in (draft_pool.find(n) for n in picks.get(m, [])) if p is not None and p.gp]
            alloc = draft_rotation(drafted, cap)
        full[m] = sum((p.per_game * alloc[p.name] for p in drafted), zero.copy())
        on_roster = {name_key(n) for n in rosters.get(m, [])}
        logged = {name_key(n): (n, g, np.asarray(t, dtype=float)) for n, (g, t) in team_log[m].items()}
        ax = {a: zero.copy() for a in ("base", "injury", "perf", "rotation", "out", "in")}
        rows, seen = [], set()
        for p in drafted:
            key = name_key(p.name)
            real = real_pool.find(p.name)
            gp_r = float(real.gp or 0) if real is not None else 0.0
            rpg = real.per_game if real is not None else zero
            share = alloc[p.name] / p.gp
            ratio = team_games.get((real.team if real is not None else None) or p.team, league_games) / 82.0
            entry = logged.get(key)
            if entry is None:      # nom du Team Log différent : rapprochement par les projections
                entry = next((v for k, v in logged.items() if real_pool.find(v[0]) is real and real is not None), None)
            name, tl_gp, tl = entry if entry else (p.name, 0.0, zero)
            seen.add(name_key(name))
            effects = {
                "base": alloc[p.name] * ratio * p.per_game,
                "injury": share * (gp_r - p.gp * ratio) * p.per_game,
                "perf": tl_gp * (rpg - p.per_game) + (tl - tl_gp * rpg),
            }
            gone = key not in on_roster and name_key(name) not in on_roster
            effects["out" if gone else "rotation"] = (tl_gp - gp_r * share) * p.per_game
            for a, v in effects.items():
                ax[a] += v
            rows.append((p.name, "parti" if gone else "drafté", alloc[p.name] * ratio, gp_r, tl_gp,
                         {a: effects.get(a, zero) for a in ("injury", "perf", "rotation", "out")}))
        for key, (name, tl_gp, tl) in logged.items():
            if key in seen:
                continue
            real = real_pool.find(name)
            ax["in"] += tl
            rows.append((name, "arrivé", 0.0, float(real.gp or 0) if real is not None else 0.0, tl_gp,
                         {"in": tl}))
        axes[m], players_of[m] = ax, rows

    def values(totals):          # totaux arrondis à l'entier, comme Yahoo et board (égalités partagées)
        return np.array([category_values(np.round(totals[m]), categories) for m in managers])

    actual = {m: sum((np.asarray(t, dtype=float) for _, t in team_log[m].values()), zero.copy()) for m in managers}
    weights = list(weights)
    summary = {m: {} for m in managers}
    exact_full = exact_points(values(full), categories, weights)
    current = {m: axes[m]["base"].copy() for m in managers}
    previous = exact_points(values(current), categories, weights)
    for i, m in enumerate(managers):
        summary[m].update(draft=exact_full[i], calendar=previous[i] - exact_full[i], expected=previous[i])
    for step, _ in STEPS:
        for m in managers:
            current[m] = current[m] + (axes[m]["out"] + axes[m]["in"] if step == "moves" else axes[m][step])
        now = exact_points(values(current), categories, weights)
        for i, m in enumerate(managers):
            summary[m][step] = now[i] - previous[i]
        previous = now
    real_points = exact_points(values(actual), categories, weights)
    for i, m in enumerate(managers):
        summary[m]["rest"] = real_points[i] - previous[i]
        summary[m]["real"] = real_points[i]

    # détail : points perdus par l'équipe si l'on retirait l'effet du joueur (autres équipes inchangées)
    base_values = values(actual)
    detail = []
    for i, m in enumerate(managers):
        for name, status, expected_games, gp_r, tl_gp, effects in players_of[m]:
            impact = {}
            for axis, vec in effects.items():
                alt = base_values.copy()
                alt[i] = category_values(actual[m] - vec, categories)
                impact[axis] = real_points[i] - exact_points(alt, categories, weights)[i]
            detail.append({"manager": m, "player": name, "status": status, "expected_games": expected_games,
                           "games": gp_r, "counted": tl_gp, "injury": impact.get("injury", 0.0),
                           "perf": impact.get("perf", 0.0), "rotation": impact.get("rotation", 0.0),
                           "moves": impact.get("out", 0.0) + impact.get("in", 0.0)})
    for d in detail:
        d["total"] = d["injury"] + d["perf"] + d["rotation"] + d["moves"]
    return {"managers": managers, "summary": summary, "detail": detail}


def rows(result, me=None, today=None):
    """Lignes de l'onglet season_analysis."""
    s = result["summary"]
    out = [[f"Analyse draft -> saison à date{' au ' + today.isoformat() if today else ''}"],
           ["Lecture", "draft (totaux de la rotation de draft sur la saison, comme board) -> calendrier (matchs déjà "
                       "joués) = attendu à date -> blessures / matchs, régime, rotation, mouvements, reste = réel "
                       "(standings Yahoo). Les étapes s'additionnent ; en points roto exacts."],
           [], SUMMARY_HEADER]
    for m in sorted(result["managers"], key=lambda x: -s[x]["real"]):
        v = s[m]
        out.append([m + (" (moi)" if m == me else ""), round(v["draft"], 1),
                    round(v["calendar"], 1), round(v["expected"], 1)]
                   + [round(v[step], 1) for step, _ in STEPS] + [round(v["rest"], 1), round(v["real"], 1)])
    out += [[], ["Détail par joueur : points roto que chaque effet a valu à l'équipe (négatif = il lui a coûté des "
                 "points), mesurés en retirant l'effet du joueur ; effets des joueurs non additifs. Mon équipe "
                 "d'abord, puis par manager, du plus grand au plus petit effet."], DETAIL_HEADER]
    order = sorted(result["detail"], key=lambda d: (d["manager"] != me, d["manager"], -abs(d["total"])))
    for d in order:
        out.append([d["manager"], d["player"], d["status"], round(d["expected_games"], 1), round(d["games"]),
                    round(d["counted"]), round(d["injury"], 1), round(d["perf"], 1), round(d["rotation"], 1),
                    round(d["moves"], 1), round(d["total"], 1)])
    return out


def read_draft_games(rows_):
    """Onglet bdd -> {manager: {joueur: matchs prévus}} (lignes de phase draft, colonnes Team Draft, Player et
    GP draft) ; None si la colonne GP draft est absente."""
    from scripts.sheets import cell_number

    header = [str(h).strip().casefold() for h in (rows_[0] if rows_ else [])]
    try:
        team, phase, player, games = (header.index(n) for n in ("team draft", "phase", "player", "gp draft"))
    except ValueError:
        return None
    out = defaultdict(dict)
    for r in rows_[1:]:
        cells = list(r) + [""] * (len(header) - len(r))
        if str(cells[phase]).strip().lower() != "draft" or not str(cells[team]).strip() or not str(cells[player]).strip():
            continue
        try:
            g = cell_number(cells[games]) if str(cells[games]).strip() else 0.0
        except ValueError:
            g = 0.0
        out[str(cells[team]).strip()][str(cells[player]).strip()] = g
    return out or None


def read_yahoo_tab(rows_):
    """Blocs draft (A), rosters (L) et Team Log (AO) de l'onglet yahoo -> picks, rosters, team_log."""
    from scripts.sheets import cell_number, column_index
    from scripts.yahoo import sheets as ys

    header = list(rows_[0]) if rows_ else []

    def col(block, name):
        start = column_index(block)
        for j in range(start, len(header)):
            if str(header[j]).strip().lower() == name.lower():
                return j
        raise ValueError(f"Onglet yahoo : colonne « {name} » absente du bloc {block}.")

    def cell(r, j):
        return r[j] if j < len(r) else ""

    def number(v):
        try:
            return cell_number(v) if str(v).strip() else 0.0
        except ValueError:
            return 0.0

    d_m, d_p = col(ys.DRAFT_START_COL, "manager"), col(ys.DRAFT_START_COL, "player")
    r_m, r_p = col(ys.ROSTERS_START_COL, "manager"), col(ys.ROSTERS_START_COL, "player")
    l_m, l_p, l_gp = col(ys.TEAM_LOG_START_COL, "manager"), col(ys.TEAM_LOG_START_COL, "player"), \
        col(ys.TEAM_LOG_START_COL, "GP")
    stat_cols = {s: col(ys.TEAM_LOG_START_COL, h) for s, h in (
        ("fgm", "FGM"), ("fga", "FGA"), ("ftm", "FTM"), ("fta", "FTA"), ("fg3m", "3PTM"), ("reb", "REB"),
        ("ast", "AST"), ("stl", "ST"), ("blk", "BLK"), ("tov", "TO"), ("pts", "PTS"))}
    picks, rosters, team_log = defaultdict(list), defaultdict(list), defaultdict(dict)
    for r in rows_[1:]:
        if str(cell(r, d_m)).strip() and str(cell(r, d_p)).strip():
            picks[str(cell(r, d_m)).strip()].append(str(cell(r, d_p)).strip())
        if str(cell(r, r_m)).strip() and str(cell(r, r_p)).strip():
            rosters[str(cell(r, r_m)).strip()].append(str(cell(r, r_p)).strip())
        if str(cell(r, l_m)).strip() and str(cell(r, l_p)).strip():
            totals = np.array([number(cell(r, stat_cols[s])) for s in STATS])
            team_log[str(cell(r, l_m)).strip()][str(cell(r, l_p)).strip()] = (number(cell(r, l_gp)), totals)
    return picks, rosters, team_log
