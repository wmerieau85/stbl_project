"""Mise en forme du module saison : onglet season, détail des effectifs (CSV), résumé console."""

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
    conf = league.get("season", {})
    st_days = int(conf.get("st_days", 15))
    rows = [[f"Saison - projection au {result['today'].isoformat()}"],
            ["Calcul", f"stats réelles + phase {conf.get('st_phase', 'st')} sur {st_days} jours + phase "
                       f"{conf.get('lt_phase', 'lt')} sur le reste de la saison"],
            ["Hypothèses", "chaque match joué est compté en priorité pour les meilleurs joueurs (valeur par match), "
                           "dans la limite des plafonds G / F / C / Util ; effectifs figés à la date du calcul"],
            ["Ce n'est pas", "une optimisation de l'alignement quotidien ni des ajouts / retraits de joueurs "
                             "(modules rotation et waivers, à venir)"],
            []]

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
        rows += my_roster_rows(teams[me], cats)
        rows.append([])

    rows.append(["Stats projetées en fin de saison"])
    rows.append(["Manager", "GP réels"] + labels)
    for i in order:
        rows.append([teams[i].manager, teams[i].actual_gp or ""] +
                    [_fmt(c, proj["values"][i, j]) for j, c in enumerate(cats)])
    return rows


def _contribution(totals, cat):
    if cat == "fgp":
        return round(totals[S["fgm"]] / totals[S["fga"]], 3) if totals[S["fga"]] else ""
    if cat == "ftp":
        return round(totals[S["ftm"]] / totals[S["fta"]], 3) if totals[S["fta"]] else ""
    return round(float(totals[S[cat]]), 1)


def my_roster_rows(team, cats):
    """Bloc « Mon effectif » : matchs et apport de chaque joueur d'ici la fin de la saison."""
    labels = [CATEGORY_LABELS.get(c, c) for c in cats]
    rows = [[f"Mon effectif ({team.manager}) : matchs et apport d'ici la fin de la saison"],
            ["Joueur", "Postes", "NBA", "Poste Yahoo", "Matchs au calendrier", "Matchs attendus", "Matchs retenus",
             "Perdus (plafonds)", "Valeur/match"] + labels]
    for p in sorted(team.players, key=lambda x: -x.value):
        lost = max(0.0, p.games_expected - p.games_used)
        rows.append([p.display or p.name, p.positions, p.nba_team, p.slot, p.games_sched,
                     round(p.games_expected, 1), round(p.games_used, 1), round(lost, 1),
                     round(p.value, 2) if p.found else "sans projection"]
                    + [_contribution(p.totals, c) for c in cats])
    return rows


def roster_rows(result):
    rows = [["Manager", "Équipe Yahoo", "Joueur", "Postes", "NBA", "Poste Yahoo", "Projection trouvée",
             "Matchs au calendrier", "Matchs attendus", "Matchs retenus", "Valeur/match (lt)"]]
    for t in result["teams"]:
        for p in sorted(t.players, key=lambda x: -x.value):
            rows.append([t.manager, t.team_name, p.display, p.positions, p.nba_team, p.slot,
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
