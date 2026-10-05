"""Mise en forme de la recommandation : onglet Google Sheets, CSV et console."""

from datetime import datetime

import numpy as np

from scripts.draft.pool import CATEGORY_LABELS

TOP_N = 20


def _pct(value):
    return "" if value is None else round(100 * value)


def _fmt(value, nd=1):
    return "" if value is None else round(float(value), nd)


def _slot_label(state, overall):
    if overall is None:
        return "-"
    rnd, pick = state.slot(overall)
    return f"tour {rnd}, choix {pick} (n°{overall + 1})"


def build_rows(reco, state, league):
    """Lignes de l'onglet de recommandation."""
    cats = [CATEGORY_LABELS[c] for c in reco.categories]
    rows = [[f"Assistant de draft STBL - {league.get('format', 'roto').upper()} - mis à jour le "
             f"{datetime.now():%d/%m/%Y %H:%M:%S}"]]
    if reco.current is None:
        rows.append(["Draft terminée."])
    else:
        rows.append(["Choix en cours", _slot_label(state, reco.current), "Au tour de", state.team_at(reco.current)])
        wait = "" if reco.my_pick is None else reco.my_pick - reco.current
        rows.append(["Mon prochain choix", _slot_label(state, reco.my_pick), "Choix avant le mien", wait])
        rows.append(["Choix suivant", _slot_label(state, reco.my_next)])
    if state.unknown:
        rows.append(["Noms non reconnus", ", ".join(state.unknown)])
    if state.duplicates:
        rows.append(["Saisis deux fois", ", ".join(state.duplicates)])
    rows.append([])

    if reco.candidates:
        best = next((c.mean_points for c in reco.candidates if c.mean_points is not None), None)
        rows.append(["RECOMMANDATIONS", f"{reco.draws} tirages", f"{reco.finalists} candidat(s) jusqu'au bout"])
        rows.append(["#", "Joueur", "Pos", "Équipe", "Pts roto espérés", "Écart vs n°1",
                     "Dispo à mon choix %", "Dispo au choix suivant %", "Z TOT", "Rang TOT", "ADP",
                     "Postes OK"] + cats + ["± pts (95 %)", "Tirages"])
        for i, c in enumerate(reco.candidates[:TOP_N], 1):
            p = c.player
            mean = c.mean_points
            rows.append([
                i, p.name, p.positions, p.team, _fmt(mean, 2),
                "" if mean is None or best is None else _fmt(mean - best, 2),
                _pct(c.p_now), _pct(c.available_next), _fmt(p.value_tot, 2), p.rank_tot,
                _fmt(p.adp, 1), "oui" if c.feasible else "NON",
            ] + ([_fmt(v, 1) for v in c.mean_cat] if c.mean_cat is not None else [""] * len(cats))
                + ["" if c.runs < 2 else _fmt(1.96 * c.std_error, 2), c.runs])
        rows.append([])

    table = reco.standings if reco.standings is not None else reco.standings_now
    title = ("CLASSEMENT ROTO PROJETÉ EN FIN DE DRAFT (si je prends le n°1)" if reco.standings is not None
             else "CLASSEMENT ROTO DES EFFECTIFS ACTUELS")
    rows += standings_rows(title, table, state, cats)
    rows.append([])
    rows += standings_rows("CLASSEMENT ROTO DES EFFECTIFS ACTUELS", reco.standings_now, state, cats,
                           values=reco.totals_now, categories=reco.categories) if reco.standings is not None else []
    rows.append([])
    rows.append(["MON ÉQUIPE", state.my_team])
    rows.append(["Joueur", "Pos", "Équipe", "GP", "Z TOT", "Rang TOT", "Origine"])
    for p in state.keepers.get(state.my_team, []):
        rows.append([p.name, p.positions, p.team, round(p.gp or 0), _fmt(p.value_tot, 2), p.rank_tot, "keeper"])
    for pk in sorted(state.picks.values(), key=lambda x: x.overall):
        if pk.team == state.my_team:
            p = pk.player
            if p is None:
                rows.append([pk.name, "", "", "", "", "", f"tour {pk.round} (non reconnu ou en double)"])
            else:
                rows.append([p.name, p.positions, p.team, round(p.gp or 0), _fmt(p.value_tot, 2), p.rank_tot,
                             f"tour {pk.round}"])
    return rows


def standings_rows(title, points, state, cats, values=None, categories=None):
    rows = [[title], ["Rang", "Manager", "Total"] + cats]
    totals = points.sum(axis=1)
    order = np.argsort(-totals, kind="stable")
    for rank, t in enumerate(order, 1):
        team = state.order[t]
        label = f"{team} (moi)" if team == state.my_team else team
        rows.append([rank, label, _fmt(totals[t], 1)] + [_fmt(v, 1) for v in points[t]])
    if values is not None:
        rows.append([])
        rows.append(["Totaux projetés", "Manager", ""] + cats)
        for t in order:
            rows.append(["", state.order[t], ""] + [
                _fmt(v, 3) if c in ("fgp", "ftp") else _fmt(v, 0) for c, v in zip(categories, values[t])])
    return rows


def console_summary(reco, state, top=10):
    lines = []
    if reco.current is None:
        lines.append("Draft terminée.")
    else:
        lines.append(f"Choix en cours : {_slot_label(state, reco.current)} -> {state.team_at(reco.current)}")
        lines.append(f"Mon prochain choix : {_slot_label(state, reco.my_pick)} ({reco.draws} tirages, "
                     f"{reco.finalists} candidat(s) jusqu'au bout)")
    for i, c in enumerate(reco.candidates[:top], 1):
        mean = "-" if c.mean_points is None else f"{c.mean_points:6.2f}"
        nxt = "" if c.available_next is None else f" | dispo choix suivant {100 * c.available_next:3.0f}%"
        lines.append(f"{i:2d}. {c.player.name:<28} {c.player.positions:<8} pts {mean} ({c.runs:3d} t.) | dispo {100 * c.p_now:3.0f}%"
                     f"{nxt}{'' if c.feasible else ' | POSTES KO'}")
    if state.unknown:
        lines.append("Noms non reconnus : " + ", ".join(state.unknown))
    return "\n".join(lines)
