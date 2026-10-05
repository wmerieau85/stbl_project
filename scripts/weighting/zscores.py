"""Z-scores des 9 catégories, en moyenne par match (AVG) et en totaux (TOT).

Paramètres dans l'onglet settings :
- teams x somme du roster = taille du groupe de joueurs draftés (15 x 14 = 210) ;
- categories : poids de chaque catégorie dans la somme (0 = catégorie ignorée / punt) ;
- zscore.min_gp : matchs projetés minimum pour entrer dans le groupe de référence ;
- zscore.iterations : nombre de passes (groupe recalculé sur les N meilleurs à chaque passe).

Méthode, pour chaque mode (AVG puis TOT) :
- catégories de comptage : z = (valeur - moyenne du groupe) / écart-type du groupe ;
- TO : même calcul, signe inversé (moins de pertes de balle = mieux) ;
- FG% / FT% : on note l'impact, (pourcentage - pourcentage du groupe) x tentatives,
  pour qu'un 60 % sur 3 tirs pèse moins qu'un 52 % sur 20 tirs ; puis z-score de l'impact ;
- somme = Σ poids x z ; rang 1 = meilleure somme.
Le groupe de référence démarre avec tous les joueurs éligibles, puis se resserre sur les
N meilleurs selon la somme, `iterations` fois.
"""

import logging
from statistics import mean, pstdev

log = logging.getLogger(__name__)

CATEGORIES = ("fgp", "fg3m", "ftp", "reb", "ast", "stl", "blk", "tov", "pts")
PERCENTS = {"fgp": ("fgm", "fga"), "ftp": ("ftm", "fta")}
NEGATIVE = {"tov"}
MODES = ("avg", "tot")


def zscore_columns():
    cols = []
    for mode in MODES:
        cols += [f"z_{cat}_{mode}" for cat in CATEGORIES] + [f"z_sum_{mode}", f"rank_{mode}"]
    return cols


def pool_size(league):
    return int(league["teams"]) * sum(int(n) for n in league["roster"].values())


def _raw(player, cat, mode):
    """Valeur de base d'une catégorie (par match si AVG, total si TOT)."""
    gp = player.get("gp") or 0
    scale = (1.0 / gp) if (mode == "avg" and gp) else 1.0
    if cat in PERCENTS:
        made, att = PERCENTS[cat]
        return player.get(made), player.get(att), scale
    value = player.get(cat)
    return (None if value is None else value * scale), None, scale


def apply_params(players, params, weights, mode):
    """z_<cat>_<mode> et z_sum_<mode> de chaque joueur, avec les paramètres (moyenne, écart-type)
    d'un groupe de référence."""
    for p in players:
        total = 0.0
        for cat in CATEGORIES:
            league_pct, mu, sd = params[cat]
            first, att, scale = _raw(p, cat, mode)
            if cat in PERCENTS:
                value = ((first or 0) - league_pct * att) * scale if att else 0.0
            else:
                value = first
            z = None if value is None or not sd else (value - mu) / sd
            if z is not None and cat in NEGATIVE:
                z = -z
            p[f"z_{cat}_{mode}"] = z
            total += weights[cat] * (z or 0.0)
        p[f"z_sum_{mode}"] = total


def reference_params(players, league):
    """Paramètres des z-scores (par mode) calculés sur ces joueurs, sans modifier la liste fournie."""
    import copy
    work = copy.deepcopy(players)
    return {mode: _compute_mode(work, league, mode) for mode in MODES}


def _compute_mode(players, league, mode):
    weights = {cat: float(league["categories"].get(cat, 0)) for cat in CATEGORIES}
    size = pool_size(league)
    min_gp = float(league.get("zscore", {}).get("min_gp", 0) or 0)
    iterations = max(1, int(league.get("zscore", {}).get("iterations", 3)))
    eligible = [p for p in players if (p.get("gp") or 0) > 0 and (p.get("gp") or 0) >= min_gp]
    pool = eligible

    for _ in range(iterations):
        stats = {}
        for cat in CATEGORIES:
            if cat in PERCENTS:
                made = [_raw(p, cat, mode) for p in pool]
                total_made = sum((m or 0) * s for m, a, s in made if m is not None and a)
                total_att = sum(a * s for m, a, s in made if m is not None and a)
                league_pct = total_made / total_att if total_att else 0
                values = [((m or 0) - league_pct * a) * s for m, a, s in made if a]
                stats[cat] = (league_pct, values)
            else:
                stats[cat] = (None, [v for v, _, _ in (_raw(p, cat, mode) for p in pool) if v is not None])
        params = {}
        for cat, (league_pct, values) in stats.items():
            mu = mean(values) if values else 0.0
            sd = pstdev(values) if len(values) > 1 else 0.0
            params[cat] = (league_pct, mu, sd)

        apply_params(players, params, weights, mode)

        pool = sorted(eligible, key=lambda p: p[f"z_sum_{mode}"], reverse=True)[:size]

    ranked = sorted(players, key=lambda p: p[f"z_sum_{mode}"], reverse=True)
    for rank, p in enumerate(ranked, 1):
        p[f"rank_{mode}"] = rank
    return params


def add_zscores(players, league):
    """Ajoute z_<cat>_avg/tot, z_sum_avg/tot et rank_avg/tot à chaque joueur (en place)."""
    unknown = [c for c in league["categories"] if c not in CATEGORIES]
    if unknown:
        raise ValueError(f"Configuration : catégories inconnues {unknown} (attendu : {', '.join(CATEGORIES)})")
    if str(league.get("format", "h2h")).lower() not in ("h2h", "roto"):
        raise ValueError("Configuration : format attendu 'h2h' ou 'roto'.")
    for mode in MODES:
        params = _compute_mode(players, league, mode)
        log.debug("Z-scores %s : %s", mode, params)
    log.info("[Z-scores] %d joueurs évalués, groupe de référence : %d joueurs (%d équipes x %d), format %s.",
             len(players), pool_size(league), int(league["teams"]),
             sum(int(n) for n in league["roster"].values()), league.get("format", "h2h").upper())
