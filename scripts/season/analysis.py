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
    + rotation    : gestion du banc pendant la détention (matchs comptés par rapport aux matchs joués chez
                    l'équipe x part prévue à la draft)
    + échanges    : apport des joueurs reçus en échange, moins les matchs perdus des joueurs draftés échangés
    + joueurs libres : idem pour les joueurs ajoutés / lâchés
    + reste       : ce qui n'est attribué à aucun axe (joueurs draftés sans projection...)
    = réel (standings Yahoo, recalculés depuis le Team Log)

Les étapes sont enchaînées dans cet ordre (en roto les effets se chevauchent : l'ordre compte un peu) et
s'additionnent exactement. Le détail par joueur donne, pour chaque axe, les points que l'équipe perdrait si
l'on retirait cet effet du joueur (les effets des joueurs ne s'additionnent pas exactement). Le bilan des
mouvements donne, pour chaque transaction, les points roto gagnés depuis par rapport à garder le partant :
apport de l'arrivant pendant ce séjour, contre production réelle du partant depuis son départ x part de ses
matchs que l'équipe comptait.

Données : phases draft et season (stats réelles) de la base ; onglet yahoo (blocs draft, rosters, Team Log et
transactions) ; colonne GP draft de l'onglet bdd ; matchs joués à chaque date : photos quotidiennes des stats
de history.sqlite (à défaut, prorata du calendrier NBA, à défaut prorata des jours).
Sans transactions, un joueur drafté parti voit ses matchs non comptés classés en joueurs libres, un joueur
toujours là en rotation.
"""
import bisect
import logging
from collections import defaultdict
from datetime import date

import numpy as np

from scripts.draft.engine import LOWER_IS_BETTER, category_values
from scripts.draft.pool import STATS
from scripts.names import name_key

log = logging.getLogger(__name__)

STEPS = (("injury", "Blessures / matchs"), ("perf", "Régime"), ("rotation", "Rotation"),
         ("trade", "Échanges"), ("fa", "Joueurs libres"))
SUMMARY_HEADER = ["Manager", "Draft (saison)", "Calendrier", "Attendu à date"] + \
    [label for _, label in STEPS] + ["Reste", "Réel (Yahoo)"]
DETAIL_HEADER = ["Manager", "Joueur", "Statut", "Matchs comptés prévus à date", "Matchs joués (NBA)",
                 "Matchs joués chez l'équipe", "Matchs comptés", "Blessures / matchs (pts)", "Régime (pts)",
                 "Rotation (pts)", "Échanges (pts)", "Joueurs libres (pts)", "Total (pts)"]
MOVES_HEADER = ["Manager", "Date", "Type", "Arrivée(s)", "Départ(s)", "Points roto gagnés depuis"]
MOVE_TYPES = {"trade": "échange", "fa": "joueur libre"}
START, NOW = "", "9999-12-31"          # bornes d'un séjour : début de saison, toujours là


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


class Timeline:
    """Matchs joués par un joueur avant une date (transaction du jour : les matchs du soir vont au nouveau
    propriétaire). history : {name_key: [(jour, matchs cumulés)]} (photos du matin : matchs de la veille) ;
    sinon prorata du calendrier (games : [(jour, domicile, extérieur)]) ; sinon prorata des jours."""

    def __init__(self, history=None, games=None, today=None, start=None):
        self.history = {k: sorted(v) for k, v in (history or {}).items()}
        self.games = sorted(games or [])
        self.today = str(today or "")
        days = [v[0][0] for v in self.history.values() if v] + [g[0] for g in self.games[:1]]
        self.start = str(start or (min(days) if days else ""))

    def before(self, keys, day, gp_now, team=None):
        if day in (START, "") or (self.start and day <= self.start):
            return 0.0
        if day >= NOW or (self.today and day > self.today):
            return float(gp_now)
        for key in keys:
            points = self.history.get(key)
            if points:
                k = bisect.bisect_right([d for d, _ in points], day) - 1
                return min(float(gp_now), float(points[k][1])) if k >= 0 else 0.0
        if self.games and team:
            from scripts.season.schedule import team_code
            code = team_code(team)
            done = sum(1 for d, h, a in self.games if code in (h, a) and (not self.today or d < self.today))
            past = sum(1 for d, h, a in self.games if code in (h, a) and d < day)
            if done:
                return float(gp_now) * min(1.0, past / done)
        if self.start and self.today and self.today > self.start:
            span = (date.fromisoformat(self.today[:10]) - date.fromisoformat(self.start[:10])).days
            gone = (date.fromisoformat(day[:10]) - date.fromisoformat(self.start[:10])).days
            return float(gp_now) * min(1.0, max(0.0, gone / span)) if span > 0 else 0.0
        return 0.0


def read_games_history(path, season, source="FantasyPros"):
    """history.sqlite -> {name_key: [(jour, matchs cumulés)]} (photos raw sea de la source)."""
    import sqlite3

    out = defaultdict(list)
    conn = sqlite3.connect(path)
    try:
        snaps = conn.execute("SELECT snapshot_id, substr(as_of, 1, 10) FROM snapshots WHERE kind='raw' AND "
                             "source=? AND season=? AND stage='sea' ORDER BY as_of", (source, season)).fetchall()
        for sid, day in snaps:
            for key, gp in conn.execute("SELECT name_key, gp FROM snapshot_rows WHERE snapshot_id=?", (sid,)):
                if key:
                    out[key].append((day, float(gp or 0)))
    finally:
        conn.close()
    return dict(out)


def _stays(transactions, drafted, ident):
    """{(manager, clé joueur): [séjour]} ; séjour = {start, end, how_in, how_out, tid_in, tid_out}."""
    own = defaultdict(list)
    for m, names in drafted.items():
        for n in names:
            own[(m, ident(n))].append({"start": START, "end": NOW, "how_in": "draft", "how_out": None,
                                       "tid_in": None, "tid_out": None})

    def close(m, key, day, how, tid):
        for st in own[(m, key)]:
            if st["end"] == NOW:
                st.update(end=day, how_out=how, tid_out=tid)
                return
        own[(m, key)].append({"start": START, "end": day, "how_in": "draft", "how_out": how, "tid_in": None,
                              "tid_out": tid})       # détenu sans trace (draft hors plan, transaction manquante)

    def open_(m, key, day, how, tid):
        own[(m, key)].append({"start": day, "end": NOW, "how_in": how, "how_out": None, "tid_in": tid,
                              "tid_out": None})

    for t in transactions:
        key, day, kind = ident(t["player"]), t["date"], t["type"]
        if kind == "add":
            open_(t["manager"], key, day, "fa", t["tid"])
        elif kind == "drop":
            close(t["manager"], key, day, "fa", t["tid"])
        elif kind == "trade":
            if t.get("other_manager"):
                close(t["other_manager"], key, day, "trade", t["tid"])
            open_(t["manager"], key, day, "trade", t["tid"])
    return own


def analyze(draft_pool, real_pool, picks, rosters, team_log, cap, categories, weights, draft_games=None,
            transactions=None, timeline=None):
    """picks / rosters : {manager: [joueurs]} ; team_log : {manager: {joueur: (matchs, totaux STATS)}} ;
    draft_games : {manager: {joueur: matchs prévus à la draft}} (colonne GP draft de bdd), sinon rotation de
    l'assistant de draft ; transactions : [{date, type (add / drop / trade), manager, other_manager, player,
    transaction}] dans l'ordre chronologique (read_transactions) ; timeline : Timeline des matchs joués.

    Retourne {"managers", "summary": {manager: {étape: points}}, "detail": [lignes joueur],
    "moves": [bilan par transaction]}."""
    zero = np.zeros(len(STATS))
    team_games = defaultdict(float)
    for p in real_pool.players:
        team_games[p.team] = max(team_games[p.team], float(p.gp or 0))
    league_games = np.mean([g for g in team_games.values() if g]) if team_games else 0.0
    managers = sorted(team_log)
    dated = transactions is not None
    timeline = timeline or Timeline()

    def ident(name):
        real = real_pool.find(name)
        return name_key(real.name) if real is not None else name_key(name)

    def now_games(name):
        real = real_pool.find(name)
        return (float(real.gp or 0), real.team) if real is not None else (0.0, None)

    def stay_games(name, stay):
        gp, team = now_games(name)
        keys = [ident(name), name_key(name)]
        return max(0.0, timeline.before(keys, stay["end"], gp, team) - timeline.before(keys, stay["start"], gp, team))

    plans, drafted_of = {}, {}
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
        plans[m], drafted_of[m] = alloc, drafted
    tx = []
    for k, t in enumerate(transactions or []):
        tx.append(dict(t, tid=(t.get("transaction") or f"#{k}", t.get("date", "")), date=str(t.get("date", ""))))
    own = _stays(tx, {m: [p.name for p in drafted_of[m]] for m in managers}, ident) if dated else {}

    full, axes, players_of, logged_of, counted_of = {}, {}, {}, {}, {}
    for m in managers:
        alloc, drafted = plans[m], drafted_of[m]
        full[m] = sum((p.per_game * alloc[p.name] for p in drafted), zero.copy())
        on_roster = {name_key(n) for n in rosters.get(m, [])}
        logged = {name_key(n): (n, g, np.asarray(t, dtype=float)) for n, (g, t) in team_log[m].items()}
        logged_of[m] = logged
        ax = {a: zero.copy() for a in ("base", "injury", "perf", "rotation", "trade", "fa")}
        rows, seen = [], set()
        held = counted = 0.0
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
            if dated:
                stays = own.get((m, ident(p.name)), [])
                owned = sum(stay_games(p.name, st) for st in stays)
                outs = [st["how_out"] for st in stays if st["how_out"]]
                gone = bool(outs) and all(st["end"] != NOW for st in stays)
                effects["rotation"] = (tl_gp - owned * share) * p.per_game
                effects["trade" if outs and outs[0] == "trade" else "fa"] = -(gp_r - owned) * share * p.per_game
                status = ("parti (" + MOVE_TYPES[outs[-1]] + ")") if gone else "drafté"
            else:
                gone = key not in on_roster and name_key(name) not in on_roster
                owned = tl_gp if gone else gp_r
                effects["fa" if gone else "rotation"] = (tl_gp - gp_r * share) * p.per_game
                status = "parti" if gone else "drafté"
            held += owned
            counted += tl_gp
            for a, v in effects.items():
                ax[a] += v
            rows.append((name, status, alloc[p.name] * ratio, gp_r, owned, tl_gp,
                         {a: effects.get(a, zero) for a in ("injury", "perf", "rotation", "trade", "fa")}))
        for key, (name, tl_gp, tl) in logged.items():
            if key in seen:
                continue
            gp_r = now_games(name)[0]
            stays = [st for st in own.get((m, ident(name)), []) if st["how_in"] in MOVE_TYPES] if dated else []
            games = [stay_games(name, st) for st in stays]
            owned = sum(games)
            split = defaultdict(float)
            for st, g in zip(stays, games):
                split[st["how_in"]] += g / owned if owned else 1.0 / len(stays)
            if not split:
                split["fa"] = 1.0
            for how, f in split.items():
                ax[how] += tl * f
            held += owned
            counted += tl_gp
            kind = max(split, key=split.get)
            rows.append((name, "arrivé (" + MOVE_TYPES[kind] + ")" if dated else "arrivé", 0.0, gp_r,
                         owned if dated else tl_gp, tl_gp, {how: tl * f for how, f in split.items()}))
        axes[m], players_of[m] = ax, rows
        counted_of[m] = counted / held if held else 1.0

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
            current[m] = current[m] + axes[m][step]
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

    def worth(i, m, vec):
        alt = base_values.copy()
        alt[i] = category_values(np.round(actual[m] - vec), categories)
        return real_points[i] - exact_points(alt, categories, weights)[i]

    detail = []
    axes_detail = ("injury", "perf", "rotation", "trade", "fa")
    for i, m in enumerate(managers):
        for name, status, expected_games, gp_r, owned, tl_gp, effects in players_of[m]:
            d = {"manager": m, "player": name, "status": status, "expected_games": expected_games,
                 "games": gp_r, "owned": owned, "counted": tl_gp}
            d.update({a: worth(i, m, effects[a]) if a in effects else 0.0 for a in axes_detail})
            d["total"] = sum(d[a] for a in axes_detail)
            detail.append(d)
    moves = _move_review(tx, own, managers, logged_of, counted_of, plans, drafted_of, real_pool, ident,
                         stay_games, now_games, timeline, worth) if dated else []
    return {"managers": managers, "summary": summary, "detail": detail, "moves": moves}


def _move_review(tx, own, managers, logged_of, counted_of, plans, drafted_of, real_pool, ident, stay_games,
                 now_games, timeline, worth):
    """Bilan de chaque transaction : points roto que l'équipe perdrait sans elle (apport de l'arrivant pendant
    ce séjour, remplacé par la production du partant depuis son départ x part de ses matchs comptée)."""
    zero = np.zeros(len(STATS))
    index = {m: i for i, m in enumerate(managers)}
    groups = {}
    for t in tx:
        sides = [(t["manager"], "in")]
        if t["type"] == "trade" and t.get("other_manager"):
            sides.append((t["other_manager"], "out"))
        elif t["type"] == "drop":
            sides = [(t["manager"], "out")]
        for m, side in sides:
            g = groups.setdefault((m, t["tid"]), {"date": t["date"], "type": MOVE_TYPES.get(
                "trade" if t["type"] == "trade" else "fa"), "in": [], "out": []})
            g[side].append(t["player"])
    out = []
    for (m, tid), g in groups.items():
        if m not in index:
            continue
        logged = logged_of[m]
        gain, cost = zero.copy(), zero.copy()
        for n in g["in"]:
            entry = logged.get(name_key(n)) or next(
                (v for v in logged.values() if ident(v[0]) == ident(n)), None)
            if entry is None:
                continue
            stays = own.get((m, ident(n)), [])
            games = [stay_games(n, st) for st in stays]
            total = sum(games)
            this = sum(gm for st, gm in zip(stays, games) if st["tid_in"] == tid)
            gain += entry[2] * (this / total if total else (1.0 if any(st["tid_in"] == tid for st in stays) else 0.0))
        for n in g["out"]:
            real = real_pool.find(n)
            if real is None or not real.gp:
                continue
            gp, team = now_games(n)
            after = gp - timeline.before([ident(n), name_key(n)], g["date"], gp, team)
            stays = own.get((m, ident(n)), [])
            owned = sum(stay_games(n, st) for st in stays)
            entry = logged.get(name_key(n)) or next((v for v in logged.values() if ident(v[0]) == ident(n)), None)
            if owned and entry is not None:
                share = entry[1] / owned
            else:
                planned = next((p for p in drafted_of[m] if ident(p.name) == ident(n)), None)
                share = plans[m][planned.name] / planned.gp if planned is not None else counted_of[m]
            cost += real.per_game * max(0.0, after) * min(1.0, share)
        out.append({"manager": m, "date": g["date"], "type": g["type"], "in": g["in"], "out": g["out"],
                    "points": worth(index[m], m, gain - cost)})
    return out


def rows(result, me=None, today=None):
    """Lignes de l'onglet season_analysis : synthèse, bilan des mouvements, détail par joueur."""
    s = result["summary"]
    out = [[f"Analyse draft -> saison à date{' au ' + today.isoformat() if today else ''}"],
           ["Lecture", "draft (totaux de la rotation de draft sur la saison, comme board) -> calendrier (matchs déjà "
                       "joués) = attendu à date -> blessures / matchs, régime, rotation (gestion du banc pendant la "
                       "détention), échanges, joueurs libres, reste = réel (standings Yahoo). Les étapes "
                       "s'additionnent ; en points roto exacts."],
           [], SUMMARY_HEADER]
    for m in sorted(result["managers"], key=lambda x: -s[x]["real"]):
        v = s[m]
        out.append([m + (" (moi)" if m == me else ""), round(v["draft"], 1),
                    round(v["calendar"], 1), round(v["expected"], 1)]
                   + [round(v[step], 1) for step, _ in STEPS] + [round(v["rest"], 1), round(v["real"], 1)])
    if result.get("moves"):
        out += [[], ["Bilan des mouvements : points roto gagnés depuis la transaction par rapport à garder le "
                     "partant (apport de l'arrivant pendant ce séjour, contre production réelle du partant depuis "
                     "son départ x part de ses matchs comptée) ; bilans non additifs. Mon équipe d'abord, par "
                     "date, puis les autres du meilleur au pire."], MOVES_HEADER]
        order = sorted(result["moves"], key=lambda d: (d["manager"] != me, d["date"] if d["manager"] == me else "",
                                                       -d["points"] if d["manager"] != me else 0))
        for d in order:
            out.append([d["manager"], d["date"], d["type"], ", ".join(d["in"]) or "-", ", ".join(d["out"]) or "-",
                        round(d["points"], 1)])
    out += [[], ["Détail par joueur : points roto que chaque effet a valu à l'équipe (négatif = il lui a coûté des "
                 "points), mesurés en retirant l'effet du joueur ; effets des joueurs non additifs. Mon équipe "
                 "d'abord, puis par manager, du plus grand au plus petit effet."], DETAIL_HEADER]
    order = sorted(result["detail"], key=lambda d: (d["manager"] != me, d["manager"], -abs(d["total"])))
    for d in order:
        out.append([d["manager"], d["player"], d["status"], round(d["expected_games"], 1), round(d["games"]),
                    round(d["owned"]), round(d["counted"]), round(d["injury"], 1), round(d["perf"], 1),
                    round(d["rotation"], 1), round(d["trade"], 1), round(d["fa"], 1), round(d["total"], 1)])
    return out


def read_transactions(rows_):
    """Bloc des transactions de l'onglet yahoo (BJ) -> [{date, type, manager, other_manager, player,
    transaction}] dans l'ordre chronologique ; None si le bloc est absent ou vide. Yahoo les liste de la plus
    récente à la plus ancienne : à date égale, l'ordre des lignes est inversé."""
    from scripts.sheets import column_index
    from scripts.yahoo import sheets as ys

    header = [str(h).strip().lower() for h in (rows_[0] if rows_ else [])]
    start = column_index(ys.TRANSACTIONS_START_COL)
    if len(header) <= start or header[start] != "date":
        return None
    cols = {}
    for name in ("date", "type", "manager", "other_manager", "player", "transaction"):
        try:
            cols[name] = header.index(name, start)
        except ValueError:
            return None
    out = []
    for k, r in enumerate(rows_[1:]):
        cells = {n: str(r[j]).strip() if j < len(r) and r[j] is not None else "" for n, j in cols.items()}
        if not cells["date"] or not cells["player"] or cells["type"] not in ("add", "drop", "trade") \
                or not cells["manager"]:
            continue
        cells["date"] = _iso_day(cells["date"])
        cells["_row"] = k
        out.append(cells)
    if not out:
        return None
    out.sort(key=lambda t: (t["date"], -t["_row"]))
    for t in out:
        del t["_row"]
    return out


def _iso_day(text):
    """« 2026-10-22 » ou date du classeur (22/10/2026) -> AAAA-MM-JJ."""
    text = text[:10]
    if len(text) == 10 and text[2] == "/" and text[5] == "/":
        return f"{text[6:10]}-{text[3:5]}-{text[0:2]}"
    return text


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
