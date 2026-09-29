"""Mise en forme du module saison : onglet season, onglet yahoo_rosters, résumé console."""

from scripts.draft.engine import S, category_values
from scripts.draft.pool import CATEGORY_LABELS
from scripts.season.engine import BUCKETS, category_gaps

PCT = {"fgp", "ftp"}


def _fmt(cat, value):
    if value is None:
        return ""
    return round(float(value), 4 if cat in PCT else 1)


def _my_index(result):
    for i, t in enumerate(result["teams"]):
        if t.manager == result["my_team"]:
            return i
    return None


def season_rows(result, league):
    teams, cats, proj = result["teams"], result["categories"], result["projection"]
    labels = [CATEGORY_LABELS.get(c, c) for c in cats]
    order = sorted(range(len(teams)), key=lambda i: -proj["expected"][i])
    rows = [[f"Saison - projection au {result['today'].isoformat()}",
             "stats réelles + st (15 jours) + lt (reste de la saison), plafonds de matchs par poste"], []]

    rows.append(["Classement projeté"])
    rows.append(["Rang", "Manager", "Équipe Yahoo", "Pts projetés", "Chances 1er", "Chances top 3",
                 "Pts réels", "Rang réel"] + [f"Pts {l}" for l in labels])
    for rank, i in enumerate(order, 1):
        t = teams[i]
        real = t.actual_points or {}
        real_total = sum(v for v in real.values() if isinstance(v, (int, float))) if any(
            isinstance(v, (int, float)) for v in real.values()) else ""
        rows.append([rank, t.manager, t.team_name, round(float(proj["expected"][i]), 1),
                     round(float(proj["p_first"][i]), 3), round(float(proj["p_podium"][i]), 3),
                     real_total, t.actual_rank or ""] + [round(float(proj["points"][i, j]), 1) for j in range(len(cats))])
    rows.append([])

    me = _my_index(result)
    if me is not None:
        rows.append([f"Mes catégories ({teams[me].manager})"])
        rows.append(["Catégorie", "Valeur projetée", "Rang projeté", "Pour +1 pt", "Marge avant -1 pt"])
        for g in category_gaps(proj["values"], cats, me):
            rows.append([CATEGORY_LABELS.get(g["cat"], g["cat"]), _fmt(g["cat"], g["value"]), g["rank"],
                         _fmt(g["cat"], g["to_gain"]), _fmt(g["cat"], g["margin"])])
        rows.append([])
        rows.append(["Mes matchs", "Plafond restant", "Projetés", "Non utilisés"])
        t = teams[me]
        for b in BUCKETS:
            rows.append([b, round(t.caps[b], 1), round(t.used[b], 1), round(t.caps[b] - t.used[b], 1)])
        rows.append([])

    rows.append(["Stats projetées en fin de saison"])
    rows.append(["Manager", "GP réels"] + labels)
    for i in order:
        rows.append([teams[i].manager, teams[i].actual_gp or ""] +
                    [_fmt(c, proj["values"][i, j]) for j, c in enumerate(cats)])
    return rows


def roster_rows(result):
    rows = [["Manager", "Équipe Yahoo", "Joueur", "Postes", "NBA", "Poste Yahoo", "Projection trouvée",
             "Matchs au calendrier", "Matchs attendus", "Matchs retenus", "Valeur/match (lt)"]]
    for t in result["teams"]:
        for p in sorted(t.players, key=lambda x: -x.value):
            rows.append([t.manager, t.team_name, p.name, p.positions, p.nba_team, p.slot,
                         "oui" if p.found else "non", p.games_sched, round(p.games_expected, 1),
                         round(p.games_used, 1), round(p.value, 2)])
    return rows


def console(result):
    teams, proj = result["teams"], result["projection"]
    order = sorted(range(len(teams)), key=lambda i: -proj["expected"][i])
    lines = [f"Classement projeté au {result['today'].isoformat()} :"]
    for rank, i in enumerate(order, 1):
        mark = " <-" if teams[i].manager == result["my_team"] else ""
        lines.append(f"{rank:>2}. {teams[i].manager:<10} {proj['expected'][i]:6.1f} pts | 1er {proj['p_first'][i]:4.0%}"
                     f" | top 3 {proj['p_podium'][i]:4.0%}{mark}")
    return "\n".join(lines)
