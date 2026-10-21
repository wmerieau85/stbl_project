"""Pertinence des sources : chaque code des grilles draft / lt / st comparé aux stats réelles, sur les trois
niveaux de la pondération (GP, MIN, STATS), et pondérations proposées.

    python -m scripts.accuracy               # onglet sources_accuracy + CSV
    python -m scripts.accuracy --no-sheet    # console + CSV

Données : history.sqlite (scripts.history). Résultat de référence : stats réelles de la saison à date (sea)
d'une même source (celle qui a le plus de photos). Pour une photo de projections prise à une date (point de
départ), la comparaison porte sur ce qui s'est passé ensuite :
- draft : de l'ouverture de la saison à la dernière photo des stats réelles ;
- lt : de chaque point de départ (photos ros, une par semaine) à la dernière photo des stats réelles ;
- st : de chaque point de départ (fenêtres l30 / l15 / l07 / sea) aux N jours suivants (horizon court terme,
  15 jours si le réglage vaut 0).
Tant que la saison n'est pas finie, les résultats sont provisoires.

Mesures, alignées sur le moteur de pondération (moyenne pondérée de GP, des minutes par match, de la
production par minute et des pourcentages) :
- GP : part des matchs de l'équipe jouée par le joueur, projetée contre réelle, ramenée en matchs sur la
  période ; écart moyen en matchs ;
- MIN : minutes par match projetées contre réelles (joueurs avec 3 matchs au moins sur la période) ;
- STATS : production par minute de chaque catégorie de comptage (minutes réelles pondérant chaque joueur),
  FG% / FT% pondérés par les tentatives réelles ; chaque écart est divisé par l'écart-type réel de la
  catégorie (1 = un écart-type), puis moyenne des catégories (même poids pour toutes).
Comparaison sur les mêmes joueurs : ceux qui comptent (les N premiers en minutes projetées par la projection
finale, ou en minutes réelles), couverts par tous les codes qui couvrent au moins 80 % de ces joueurs. Un
code moins couvert est mesuré sur ses seuls joueurs (« couverture partielle ») et garde son poids actuel.

Pondérations : optimal = combinaison de codes (poids positifs, total 100 %) qui fait la plus petite erreur
quadratique sur l'échantillon ; proposé = rapprochement vers les poids actuels (Optim | History | Shrink to
current weights : 50 % = à mi-chemin). Rien n'est réécrit dans les grilles.
"""

import argparse
import csv
import logging
import os
import sqlite3
import sys
from collections import defaultdict
from contextlib import closing
from datetime import timedelta

import numpy as np

from scripts.config import EXPORTS_DIR, HISTORY_PATH, load_league, load_settings
from scripts.history import _day, other_season

log = logging.getLogger("stbl.accuracy")

SEASON_GAMES = 82
CATS = ("fgp", "fg3m", "ftp", "reb", "ast", "stl", "blk", "tov", "pts")
LABELS = {"fgp": "FG%", "fg3m": "3PM", "ftp": "FT%", "reb": "REB", "ast": "AST", "stl": "STL",
          "blk": "BLK", "tov": "TO", "pts": "PTS"}
PCT = {"fgp": ("fgm", "fga"), "ftp": ("ftm", "fta")}
COUNT = tuple(c for c in CATS if c not in PCT)
MIN_GAMES = 3          # matchs réels sur la période pour mesurer les minutes et les stats
MIN_MINUTES = 60.0     # minutes réelles sur la période pour mesurer la production par minute
MIN_TEAM_GAMES = 1     # matchs de l'équipe sur la période (GP)
COVERAGE = 0.80        # part des joueurs qui comptent à couvrir pour entrer dans la comparaison commune
SPACING_DAYS = 7       # écart minimal entre deux points de départ (lt, st)
MATCH_DAYS = 7         # une photo de code sert pour un point de départ jusqu'à 7 jours après sa date
SEA_TOLERANCE = 3      # photo des stats réelles acceptée jusqu'à 3 jours d'écart avec la date voulue
WEAK_PROGRESS = 0.25   # en dessous : poids proposés indicatifs (trop peu de matchs joués sur la période)
LEVELS = ("gp", "min", "stats")
LEVEL_LABELS = {"gp": "GP", "min": "MIN", "stats": "STATS"}
UNITS = {"gp": "écart moyen (matchs)", "min": "écart moyen (minutes par match)",
         "stats": "écart moyen (1 = un écart-type)"}


# --------------------------------------------------------------------------------------------- données

class History:
    """Photos d'une saison, lignes chargées à la demande : {name_key: dict}."""

    def __init__(self, conn, season):
        self.conn, self.season, self._rows = conn, season, {}
        self.snaps = [dict(zip(("id", "kind", "source", "stage", "as_of"), r)) for r in conn.execute(
            "SELECT snapshot_id, kind, source, stage, as_of FROM snapshots WHERE season=? ORDER BY as_of",
            (season,))]
        for s in self.snaps:
            s["day"] = _day(s["as_of"])

    def rows(self, sid):
        if sid not in self._rows:
            cur = self.conn.execute("SELECT * FROM snapshot_rows WHERE snapshot_id=?", (sid,))
            names = [d[0] for d in cur.description]
            out = {}
            for r in cur:
                row = dict(zip(names, r))
                key = row.get("name_key") or f"id:{row.get('player_id')}"
                if key not in out:
                    out[key] = row
            self._rows[sid] = out
        return self._rows[sid]

    def of(self, kind, source, stage):
        return [s for s in self.snaps if s["kind"] == kind and s["source"] == source and s["stage"] == stage]

    def truth_source(self):
        """Source des stats réelles de référence : la plus photographiée (FantasyPros à égalité)."""
        counts = defaultdict(int)
        for s in self.snaps:
            if s["kind"] == "raw" and s["stage"] == "sea" and self.valid_sea(s):
                counts[s["source"]] += 1
        if not counts:
            return None
        return max(counts, key=lambda src: (counts[src], src == "FantasyPros"))

    def valid_sea(self, snap):
        rows = list(self.rows(snap["id"]).values())
        tup = [(None, None, None, None, None, r.get("gp")) for r in rows]
        return not other_season(self.season, "sea", snap["as_of"], tup)


def team_games(rows):
    """Matchs joués par équipe = plus grand nombre de matchs d'un de ses joueurs."""
    out = defaultdict(float)
    for r in rows.values():
        if r.get("team") and r.get("gp"):
            out[r["team"]] = max(out[r["team"]], float(r["gp"]))
    return out


def _f(value):
    return None if value is None else float(value)


def actual_window(end, start):
    """Stats réelles entre deux photos sea (start None = ouverture de la saison) : {clé: dict}."""
    end_rows, start_rows = end, start or {}
    teams_end, teams_start = team_games(end_rows), team_games(start_rows)
    out = {}
    for key, r in end_rows.items():
        s = start_rows.get(key, {})
        row = {"team": r.get("team")}
        for f in ("gp", "min", "fg3m", "reb", "ast", "stl", "blk", "tov", "pts", "fgm", "fga", "ftm", "fta"):
            a, b = _f(r.get(f)), _f(s.get(f)) or 0.0
            row[f] = None if a is None else max(a - b, 0.0)
        if row["min"] is None and r.get("mpg") is not None and r.get("gp") is not None:
            row["min"] = float(r["mpg"]) * float(r["gp"]) - (_f(s.get("mpg") or 0) * _f(s.get("gp") or 0))
        if start is None:   # depuis l'ouverture : pourcentages directs si les tentatives manquent
            row["fgp_direct"], row["ftp_direct"] = _f(r.get("fgp")), _f(r.get("ftp"))
        out[key] = row
    teams = {t: max(g - teams_start.get(t, 0.0), 0.0) for t, g in teams_end.items()}
    return out, teams


# ------------------------------------------------------------------------------------------ prédictions

def predict(row, code, snap_rows, team_before, ref_mpg):
    """Valeurs comparables d'un code pour un joueur : part des matchs, MPG, production par minute, %."""
    gp, mpg, minutes = _f(row.get("gp")), _f(row.get("mpg")), _f(row.get("min"))
    if mpg is None and minutes is not None and gp:
        mpg = minutes / gp
    team = row.get("team")
    stage = code["stage"]
    if stage == "draft":
        denom = SEASON_GAMES
    elif code["final"] or stage == "ros":
        denom = max(SEASON_GAMES - team_before.get(team, 0.0), 1.0)
    else:   # fenêtre de stats réelles (sea, l30...) : matchs de l'équipe dans la fenêtre
        denom = team_games(snap_rows).get(team, 0.0)
    out = {"share": min(gp / denom, 1.0) if gp is not None and denom else None, "mpg": mpg}
    per_game_ok = code.get("allow_per_game_without_gp")
    for cat in COUNT:
        total = _f(row.get(cat))
        if total is None or (not gp and not per_game_ok):
            out[cat] = None
            continue
        per_game = total / gp if gp else total
        base = mpg or ref_mpg
        out[cat] = per_game / base if base else None
    for cat, (made, att) in PCT.items():
        value = _f(row.get(cat))
        if value is None and _f(row.get(made)) is not None and _f(row.get(att)):
            value = float(row[made]) / float(row[att])
        out[cat] = value
    return out


def truth_values(a, team_window):
    """Valeurs réelles comparables d'un joueur (None si la période est trop courte)."""
    games, team = a.get("gp") or 0.0, team_window.get(a.get("team"), 0.0)
    minutes = a.get("min") or 0.0
    out = {"share": games / team if team >= MIN_TEAM_GAMES else None, "team_games": team,
           "mpg": minutes / games if games >= MIN_GAMES and minutes else None, "games": games, "minutes": minutes}
    for cat in COUNT:
        out[cat] = a[cat] / minutes if minutes >= MIN_MINUTES and a.get(cat) is not None else None
    for cat, (made, att) in PCT.items():
        if minutes < MIN_MINUTES:
            out[cat], out[cat + "_w"] = None, 0.0
        elif a.get(att):
            out[cat], out[cat + "_w"] = a[made] / a[att], a[att]
        else:
            out[cat], out[cat + "_w"] = a.get(cat + "_direct"), minutes
    return out


# --------------------------------------------------------------------------------------------- périodes

def _latest(snaps, day, max_age=MATCH_DAYS):
    ok = [s for s in snaps if s["day"] <= day and (day - s["day"]).days <= max_age]
    return ok[-1] if ok else None


def _sea_at(seas, day, after=False):
    """Photo des stats réelles la plus proche de `day` (avant, ou après si after)."""
    if after:
        ok = [s for s in seas if s["day"] >= day and (s["day"] - day).days <= SEA_TOLERANCE]
        return ok[0] if ok else None
    ok = [s for s in seas if s["day"] <= day and (day - s["day"]).days <= SEA_TOLERANCE]
    return ok[-1] if ok else None


def _thin(days):
    out = []
    for d in sorted(set(days)):
        if not out or (d - out[-1]).days >= SPACING_DAYS:
            out.append(d)
    return out


def periods(hist, phase, codes, seas, horizon):
    """[(point de départ, photo sea de début ou None, photo sea de fin, {code: photo})]."""
    if not seas:
        return []
    first_sea, last_sea = seas[0], seas[-1]
    snaps = {c["code"]: hist.of("final" if c["final"] else "raw", c["source"], c["stage"]) for c in codes}
    if phase == "draft":
        chosen = {code: [x for x in s if x["day"] <= first_sea["day"]][-1]
                  for code, s in snaps.items() if any(x["day"] <= first_sea["day"] for x in s)}
        return [(first_sea["day"], None, last_sea, chosen)] if chosen else []
    starts = _thin(s["day"] for code_snaps in snaps.values() for s in code_snaps)
    out = []
    for day in starts:
        if day < first_sea["day"]:
            begin = None                     # avant le premier match : rien de joué
        else:
            begin = _sea_at(seas, day)
            if begin is None:
                continue
        end = last_sea if phase == "lt" else _sea_at(seas, day + timedelta(days=horizon), after=True)
        if end is None or (begin is not None and end["day"] <= begin["day"]):
            continue
        chosen = {code: _latest(s, day) for code, s in snaps.items()}
        chosen = {code: s for code, s in chosen.items() if s}
        if chosen:
            out.append((day, begin, end, chosen))
    return out


# ------------------------------------------------------------------------------------------- échantillon

def build_samples(hist, phase, codes, seas, horizon, players):
    """Lignes (période, joueur) : réel + prédiction de chaque code. Renvoie (lignes, périodes, avancement)."""
    samples, used, progress = [], [], []
    for day, begin, end, chosen in periods(hist, phase, codes, seas, horizon):
        begin_rows = hist.rows(begin["id"]) if begin else None
        actual, team_window = actual_window(hist.rows(end["id"]), begin_rows)
        team_before = team_games(begin_rows or {})
        # avancement : matchs joués sur la période / matchs restants au départ
        remaining = [SEASON_GAMES - team_before.get(t, 0.0) for t in team_window] or [SEASON_GAMES]
        done = [team_window[t] for t in team_window]
        if phase != "st" and done:
            progress.append(float(np.mean(done)) / max(float(np.mean(remaining)), 1.0))
        code_rows = {c["code"]: hist.rows(chosen[c["code"]]["id"]) for c in codes if c["code"] in chosen}
        final_code = next((c["code"] for c in codes if c["final"] and c["code"] in code_rows), None)
        bench = _benchmark(hist, phase, day)
        # joueurs qui comptent : minutes projetées (projection finale de la phase, sinon moyenne des codes)
        projected = defaultdict(list)
        for rows in ([bench] if bench else []) or list(code_rows.values()):
            for key, r in rows.items():
                gp, mpg = _f(r.get("gp")), _f(r.get("mpg"))
                if gp and mpg:
                    projected[key].append(gp * mpg)
        top_proj = sorted(projected, key=lambda k: -np.mean(projected[k]))[:players]
        top_real = sorted((k for k in actual if actual[k].get("min")), key=lambda k: -actual[k]["min"])[:players]
        keys = list(dict.fromkeys(top_proj + top_real))
        for key in keys:
            source_row = next((rows[key] for rows in code_rows.values() if key in rows), None)
            team = (actual.get(key) or {}).get("team") or (source_row or {}).get("team")
            a = actual.get(key) or {f: 0.0 for f in ("gp", "min")}
            a = dict(a, team=team)
            truth = truth_values(a, team_window)
            preds = {}
            mpgs = [_f(rows[key].get("mpg")) for rows in code_rows.values() if key in rows and rows[key].get("mpg")]
            ref_mpg = float(np.mean(mpgs)) if mpgs else truth.get("mpg")
            for c in codes:
                rows = code_rows.get(c["code"])
                if rows and key in rows:
                    snap = chosen[c["code"]]
                    preds[c["code"]] = predict(rows[key], c, rows, team_before, ref_mpg)
                    preds[c["code"]]["_snap"] = snap["day"]
            if bench and key in bench:
                preds["_final"] = predict(bench[key], {"stage": phase, "final": True}, bench, team_before, ref_mpg)
            samples.append({"day": day, "key": key, "truth": truth, "preds": preds})
        used.append((day, begin["day"] if begin else None, end["day"], final_code))
    return samples, used, progress


def _benchmark(hist, phase, day):
    """Projection finale de la phase photographiée au point de départ (comparaison avec le mélange)."""
    snaps = hist.of("final", phase, phase)
    snap = _latest(snaps, day, max_age=MATCH_DAYS if phase != "draft" else 365)
    return hist.rows(snap["id"]) if snap else None


# ------------------------------------------------------------------------------------------------ mesure

def level_items(samples, level, code):
    """[(prédit, réel, poids, catégorie)] d'un code (ou '_final') pour un niveau."""
    out = []
    for s in samples:
        p, t = s["preds"].get(code), s["truth"]
        if p is None:
            continue
        if level == "gp":
            if p["share"] is not None and t["share"] is not None:
                out.append((p["share"] * t["team_games"], t["share"] * t["team_games"], 1.0, "gp", s))
        elif level == "min":
            if p["mpg"] is not None and t["mpg"] is not None:
                out.append((p["mpg"], t["mpg"], t["games"], "min", s))
        else:
            for cat in CATS:
                if p.get(cat) is None or t.get(cat) is None:
                    continue
                weight = t.get(cat + "_w", 0.0) if cat in PCT else t["minutes"]
                if weight:
                    out.append((p[cat], t[cat], weight, cat, s))
    return out


def spreads(samples):
    """Écart-type réel pondéré de chaque catégorie (normalisation des écarts STATS)."""
    out = {}
    for cat in CATS:
        vals, weights = [], []
        for s in samples:
            t = s["truth"]
            w = t.get(cat + "_w", 0.0) if cat in PCT else t["minutes"]
            if t.get(cat) is not None and w:
                vals.append(t[cat])
                weights.append(w)
        if len(vals) >= 5:
            mean = np.average(vals, weights=weights)
            std = float(np.sqrt(np.average((np.array(vals) - mean) ** 2, weights=weights)))
            out[cat] = std if std > 1e-9 else None     # catégorie sans dispersion réelle : pas mesurable
        else:
            out[cat] = None
    return out


def error(items, level, sigma):
    """Écart moyen (pondéré) ; STATS : moyenne des catégories, chacune divisée par son écart-type."""
    if not items:
        return None, {}
    if level != "stats":
        p, t, w = (np.array([i[k] for i in items]) for k in (0, 1, 2))
        return float(np.average(np.abs(p - t), weights=w)), {}
    by_cat = {}
    for cat in CATS:
        sel = [i for i in items if i[3] == cat]
        if sel and sigma.get(cat):
            p, t, w = (np.array([i[k] for i in sel]) for k in (0, 1, 2))
            by_cat[cat] = float(np.average(np.abs(p - t), weights=w)) / sigma[cat]
    return (float(np.mean(list(by_cat.values()))) if by_cat else None), by_cat


def project_simplex(v):
    """Projection euclidienne sur {w >= 0, somme = 1}."""
    u = np.sort(v)[::-1]
    css = np.cumsum(u)
    rho = np.nonzero(u * np.arange(1, len(v) + 1) > (css - 1))[0][-1]
    theta = (css[rho] - 1) / (rho + 1.0)
    return np.maximum(v - theta, 0.0)


def optimal_weights(matrix, target, weights, iterations=3000):
    """Poids positifs de total 1 minimisant sum(w_i (X b - y)^2) (gradient projeté)."""
    k = matrix.shape[1]
    if k == 1:
        return np.array([1.0])
    w = np.sqrt(weights / weights.sum())
    x, y = matrix * w[:, None], target * w
    lipschitz = 2 * np.linalg.eigvalsh(x.T @ x).max()
    if lipschitz <= 0:
        return np.full(k, 1.0 / k)
    b = np.full(k, 1.0 / k)
    for _ in range(iterations):
        b = project_simplex(b - (2 * x.T @ (x @ b - y)) / lipschitz)
    return b


def design(samples, level, codes, sigma):
    """Matrice (lignes communes à tous les codes) pour l'optimisation : X, y, poids."""
    rows_x, rows_y, rows_w = [], [], []
    per_code = {c: {(id(i[4]), i[3]): i for i in level_items(samples, level, c)} for c in codes}
    common = set.intersection(*(set(d) for d in per_code.values())) if per_code else set()
    for key in common:
        cat = key[1]
        scale = 1.0 if level != "stats" else (sigma.get(cat) or 0.0)
        if level == "stats" and not scale:
            continue
        first = per_code[codes[0]][key]
        rows_x.append([per_code[c][key][0] / scale for c in codes])
        rows_y.append(first[1] / scale)
        rows_w.append(first[2] if level != "stats" else first[2] / _cat_weight_total(samples, cat))
    return np.array(rows_x), np.array(rows_y), np.array(rows_w)


def _cat_weight_total(samples, cat):
    total = sum((s["truth"].get(cat + "_w", 0.0) if cat in PCT else s["truth"]["minutes"])
                for s in samples if s["truth"].get(cat) is not None)
    return total or 1.0


def current_weight(code, level):
    w = code["weights"][level]
    if level == "stats":
        vals = list(w.values())
        return float(np.mean(vals)) if vals else 0.0
    return float(w)


def evaluate_level(samples, codes, level, sigma, shrink, players):
    """Tableau d'un niveau : une ligne par code + mélanges actuel / proposé + projection finale."""
    keys_of = {}
    for c in codes:
        keys_of[c["code"]] = {s["key"] for s in samples if c["code"] in s["preds"] and level_items([s], level, c["code"])}
    measurable = {s["key"] for s in samples if _truth_ok(s["truth"], level)}
    base = len(measurable) or 1
    coverage = {code: len(keys & measurable) / base for code, keys in keys_of.items()}
    full = [c["code"] for c in codes if coverage[c["code"]] >= COVERAGE]
    common_keys = set.intersection(*(keys_of[c] & measurable for c in full)) if full else set()
    common = [s for s in samples if s["key"] in common_keys]
    rows = []
    for c in codes:
        code = c["code"]
        sample = common if code in full else [s for s in samples if s["key"] in keys_of[code]]
        err, by_cat = error(level_items(sample, level, code), level, sigma)
        rows.append({"code": code, "source": c["label"], "coverage": coverage[code],
                     "players": len({s["key"] for s in sample if code in s["preds"]}) if err is not None else 0,
                     "error": err, "by_cat": by_cat, "partial": code not in full and err is not None,
                     "current": current_weight(c, level)})
    total_current = sum(r["current"] for r in rows) or 1.0
    for r in rows:
        r["current"] /= total_current
    optimized = [r for r in rows if r["code"] in full and r["error"] is not None]
    mass = sum(r["current"] for r in optimized)
    if len(optimized) >= 1 and common:
        x, y, w = design(common, level, [r["code"] for r in optimized], sigma)
        best = optimal_weights(x, y, w) if len(y) else np.array([r["current"] / (mass or 1) for r in optimized])
    else:
        best = np.array([])
    for r in rows:
        r["optimal"], r["proposed"] = r["current"], r["current"]
    for r, b in zip(optimized, best):
        r["optimal"] = float(b) * mass
        r["proposed"] = shrink * r["current"] + (1 - shrink) * r["optimal"]
    ranked = sorted((r for r in rows if r["code"] in full and r["error"] is not None), key=lambda r: r["error"])
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    blends = {}
    if common:
        for name, key in (("Mélange actuel", "current"), ("Mélange proposé", "proposed")):
            blends[name] = _blend_error(common, level, [(r["code"], r[key]) for r in optimized], sigma)
        final_items = level_items(common, level, "_final")
        if final_items:
            blends["Projection finale (photo)"] = error(final_items, level, sigma)
    return {"rows": rows, "blends": blends, "common": len(common_keys), "measurable": len(measurable)}


def _truth_ok(t, level):
    if level == "gp":
        return t["share"] is not None
    if level == "min":
        return t["mpg"] is not None
    return t["minutes"] >= MIN_MINUTES


def _blend_error(samples, level, weighted_codes, sigma):
    total = sum(w for _, w in weighted_codes)
    if total <= 0:
        return None, {}
    items = []
    per_code = {c: {(id(i[4]), i[3]): i for i in level_items(samples, level, c)} for c, _ in weighted_codes}
    keys = set.intersection(*(set(d) for d in per_code.values())) if per_code else set()
    for key in keys:
        first = per_code[weighted_codes[0][0]][key]
        value = sum(w * per_code[c][key][0] for c, w in weighted_codes) / total
        items.append((value, first[1], first[2], first[3], first[4]))
    return error(items, level, sigma)


# ------------------------------------------------------------------------------------------------ calcul

def compute(history_path=None, settings=None, league=None, grids=None):
    """Résultat complet : {season, truth, as_of, phases: {phase: {...}}}."""
    from scripts.weighting.weights import load_grid

    settings = settings or load_settings()
    league = league if league is not None else load_league()
    season = settings["active_season"]
    conf = league.get("history") or {}
    shrink = float(conf.get("shrink", 0.5))
    players = int(conf.get("players", 200))
    horizon = int((league.get("season") or {}).get("st_days") or 0) or 15
    out = {"season": season, "shrink": shrink, "horizon": horizon, "players": players, "phases": {}}
    path = history_path or HISTORY_PATH
    if not os.path.exists(path):
        out["error"] = f"{os.path.basename(path)} absent : lancez d'abord une photo des sources."
        return out
    with closing(sqlite3.connect(path)) as conn:
        hist = History(conn, season)
        truth = hist.truth_source()
        out["truth"] = truth
        seas = [s for s in hist.of("raw", truth, "sea") if hist.valid_sea(s)] if truth else []
        out["as_of"] = seas[-1]["day"] if seas else None
        for phase in ("draft", "lt", "st"):
            grid = (grids or {}).get(phase) or load_grid(phase, season)
            codes = []
            for slot in grid["slots"]:
                if not slot.get("code"):
                    continue
                label = slot["source"] if not slot.get("final") else f"projection finale {slot['source']}"
                codes.append(dict(slot, label=label))
            samples, used, progress = build_samples(hist, phase, codes, seas, horizon, players)
            sigma = spreads(samples)
            result = {"periods": used, "progress": float(np.mean(progress)) if progress else None,
                      "levels": {}}
            if samples:
                for level in LEVELS:
                    result["levels"][level] = evaluate_level(samples, codes, level, sigma, shrink, players)
            out["phases"][phase] = result
    return out


# ------------------------------------------------------------------------------------------------ sortie

def _pct(value):
    return None if value is None else round(100 * value, 1)


def _num(value, digits=2):
    return None if value is None else round(float(value), digits)


def rows(result):
    """Lignes de l'onglet sources_accuracy."""
    season, out = result["season"], []
    title = f"Pertinence des sources - saison {season}"
    if result.get("error"):
        return [[title], [result["error"]]]
    as_of = result.get("as_of")
    out.append([title + (f", stats réelles au {as_of:%d/%m/%Y}" if as_of else "")])
    out.append([f"Référence : stats réelles {result.get('truth') or '(aucune photo)'} (sea). Rapprochement des "
                f"poids proposés vers les poids actuels : {round(100 * result['shrink'])} %. Horizon st : "
                f"{result['horizon']} jours. Joueurs qui comptent : {result['players']} premiers en minutes "
                f"projetées ou réelles."])
    out.append(["Poids optimal = combinaison qui fait la plus petite erreur sur la période ; poids proposé = "
                "entre poids actuel et optimal. Rien n'est réécrit dans les grilles."])
    for phase, res in result["phases"].items():
        out.append([])
        periods_ = res["periods"]
        progress = res.get("progress")
        head = f"Grille {phase}"
        if not periods_ or not res["levels"]:
            out.append([head + " : pas encore de période comparable (photos des stats réelles ou des "
                        "projections à attendre)."])
            continue
        end = max(e for _, _, e, _ in periods_)
        if phase == "draft":
            scope = f"projections de pré-saison comparées aux stats réelles jusqu'au {end:%d/%m}"
        else:
            starts = ", ".join(f"{d:%d/%m}" for d, *_ in periods_[:8]) + (" ..." if len(periods_) > 8 else "")
            scope = f"{len(periods_)} point(s) de départ ({starts}), stats réelles jusqu'au {end:%d/%m}"
        if progress is None:
            state = "provisoire"
        elif progress >= 0.98:
            state = "saison complète"
        else:
            state = f"provisoire, {round(100 * progress)} % des matchs de la période joués"
        weak = phase == "st" and len(periods_) < 2 or progress is not None and progress < WEAK_PROGRESS
        out.append([f"{head} : {scope} - {state}" + (" - échantillon encore faible : poids proposés indicatifs"
                                                     if weak else "")])
        for level in LEVELS:
            lv = res["levels"].get(level)
            if not lv:
                continue
            if not lv["measurable"]:
                why = {"gp": "aucun match d'équipe sur la période",
                       "min": f"pas encore de joueur avec {MIN_GAMES} matchs sur la période",
                       "stats": f"pas encore de joueur avec {MIN_MINUTES:.0f} minutes sur la période"}[level]
                out.append([LEVEL_LABELS[level], f"pas encore mesurable : {why}"])
                continue
            header = ["Niveau", "Code", "Source", "Couverture (%)", "Joueurs", UNITS[level], "Rang",
                      "Poids actuel (%)", "Poids optimal (%)", "Poids proposé (%)"]
            if level == "stats":
                header += [f"{LABELS[c]} (écart)" for c in CATS]
            out.append(header)
            for r in lv["rows"]:
                line = [LEVEL_LABELS[level], r["code"], r["source"], _pct(r["coverage"]), r["players"],
                        _num(r["error"], 3), r.get("rank") or ("couverture partielle" if r["partial"] else ""),
                        _pct(r["current"]), _pct(r["optimal"]), _pct(r["proposed"])]
                if level == "stats":
                    line += [_num(r["by_cat"].get(c), 3) for c in CATS]
                out.append(line)
            for name, (err, by_cat) in lv["blends"].items():
                line = [LEVEL_LABELS[level], name, "", "", lv["common"], _num(err, 3), "", "", "", ""]
                if level == "stats":
                    line += [_num(by_cat.get(c), 3) for c in CATS]
                out.append(line)
    return out


def write_csv(table, season):
    os.makedirs(EXPORTS_DIR, exist_ok=True)
    path = os.path.join(EXPORTS_DIR, f"sources_accuracy_{season}.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        csv.writer(fh, delimiter=";").writerows(table)
    return path


def push_sheet(table, league, settings=None):
    from scripts.sheets import open_spreadsheet, write_tab

    gs = league.get("google_sheets") or {}
    tab = gs.get("accuracy_tab") or "sources_accuracy"
    write_tab(open_spreadsheet(gs["draft_spreadsheet_id"], settings), tab, table)
    return tab


def main(argv=None):
    parser = argparse.ArgumentParser(description="Pertinence des sources (history.sqlite)")
    parser.add_argument("--no-sheet", action="store_true", help="console + CSV seulement")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    league = load_league()
    result = compute(league=league)
    table = rows(result)
    for line in table:
        print(" | ".join("" if c is None else str(c) for c in line))
    log.info("[Accuracy] CSV : %s", write_csv(table, result["season"]))
    if not args.no_sheet:
        log.info("[Accuracy] Onglet %s écrit.", push_sheet(table, league))
    return 0


if __name__ == "__main__":
    sys.exit(main())
