"""Projection plausible des adversaires : taux d'alignement calibré sur leur comportement réel.

Ni projection basique (tous les matchs attendus comptent), ni projection optimisée (ils n'ont pas
notre outil) : chaque adversaire garde un lineup standard (meilleurs joueurs par match d'abord, sous
les plafonds G / F / C / Util), mais un match attendu n'est aligné qu'avec son taux d'alignement.

    taux observé = matchs réellement comptés (classement Yahoo, GP)
                   / matchs possibles à date
    matchs possibles à date = min(somme des matchs NBA déjà joués x probabilité de jouer de chaque
                                  joueur de l'effectif actuel,
                                  plafonds de la saison x part de la saison déjà jouée)
    taux retenu = confiance x taux observé + (1 - confiance) x taux de la ligue
    confiance   = jours écoulés / durée de montée en confiance (6 semaines par défaut), au plus 1
    taux de la ligue = médiane des taux observés (au moins 5 équipes), sinon valeur par défaut

Limite connue (version 1) : les matchs possibles sont estimés avec l'effectif actuel ; un manager qui
change souvent de joueurs (streaming) peut dépasser 100 % (ramené à 100 %), un joueur arrivé
récemment est compté comme s'il était là depuis le début.
"""

import statistics
from datetime import date

from scripts.season.engine import BUCKETS, apply_lineup_rate

MIN_POSSIBLE_GAMES = 10     # en dessous, taux observé non significatif
MIN_TEAMS_FOR_LEAGUE = 5
RATE_FLOOR = 0.3


def _season_days(games):
    days = sorted({g[0] for g in games})
    return (date.fromisoformat(days[0]), date.fromisoformat(days[-1])) if days else (None, None)


def possible_games_to_date(team, season_fraction):
    """Matchs que l'effectif actuel aurait pu faire compter depuis le début de saison."""
    roster = sum(p.games_past_sched * p.p_play for p in team.players)
    total_caps = sum(float(team.caps.get(b, 0.0)) for b in BUCKETS) + float(team.actual_gp or 0.0)
    pace = total_caps * season_fraction
    return min(roster, pace) if pace > 0 else roster


def calibrate(teams, games, today, my_manager=None, conf=None):
    """Calcule et applique le taux d'alignement de chaque adversaire. Retourne le taux de la ligue."""
    conf = conf or {}
    model = str(conf.get("opponent_model", "usage")).strip().lower()
    default_rate = float(conf.get("opponent_default_rate", 0.95))
    ramp_days = max(1, int(conf.get("opponent_ramp_days", 42)))
    first, last = _season_days(games)
    elapsed = max(0, (today - first).days) if first else 0
    season_len = max(1, (last - first).days + 1) if first else 1
    fraction = min(1.0, elapsed / season_len)
    confidence = min(1.0, elapsed / ramp_days)

    observed = {}
    for t in teams:
        possible = possible_games_to_date(t, fraction)
        rate = None
        if possible >= MIN_POSSIBLE_GAMES and t.actual_gp:
            rate = max(RATE_FLOOR, min(1.0, float(t.actual_gp) / possible))
        observed[id(t)] = (possible, rate)
    rates = [r for _, r in observed.values() if r is not None]
    league_rate = statistics.median(rates) if len(rates) >= MIN_TEAMS_FOR_LEAGUE else default_rate

    for t in teams:
        possible, rate = observed[id(t)]
        mine = t.manager == my_manager
        w = confidence if rate is not None else 0.0
        retained = w * rate + (1 - w) * league_rate if rate is not None else league_rate
        if model == "value" or mine:
            applied = 1.0
        else:
            applied = retained
        t.lineup = {"possible": possible, "observed": rate, "confidence": w, "league": league_rate,
                    "retained": retained, "applied": applied, "model": "mon équipe" if mine else model}
        if not mine:
            apply_lineup_rate(t, applied)
    return league_rate
