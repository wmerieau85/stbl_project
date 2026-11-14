"""Calcul des projections finales pondérées.

Modèle à 3 niveaux, chacun pondéré par sa ligne de la grille :

    Total d'une stat = GP  x  MPG  x  production par minute
                       (GP)   (MIN)   (STATS)

- GP  : moyenne pondérée des matchs joués.
- MPG : moyenne pondérée des minutes par match.
- STATS : moyenne pondérée de la production par minute de chaque source.
  Une source sans minutes (ex. DraftKick) est ramenée à la minute avec le MPG final :
  sa moyenne par match compte alors telle quelle.
- FG% / FT% : moyenne des pourcentages pondérée par le poids de la grille x les tentatives par match
  de chaque source (une source à 0/0, ou sans match, est ignorée) ;
  les tentatives (FGA, FTA) viennent des sources qui les fournissent ;
  FGM = FG% x FGA et FTM = FT% x FTA, donc cohérents avec les pourcentages.
  Sans aucune source de tentatives : FGA estimé par
  FGA = (PTS - 3PM) / (2 x FG% + FT% x r), FTA = r x FGA, r = ratio FTA/FGA médian.

Si une source ne couvre pas un joueur (ou une stat), les poids des sources présentes
sont renormalisés ; les colonnes *_coverage indiquent la part du poids réellement présente.

Blessés (onglet injuries / ESPN), phases lt et st : avec une date de retour, les sources du niveau GP qui
projettent plus de matchs que l'équipe n'en jouera après le retour (ratio > Injury GP ratio) sont retirées
du niveau GP, puis le GP est plafonné aux matchs restants ; absent jusqu'à la fin de saison : tout à 0.
"""

import hashlib
import json
import logging
from datetime import datetime
from statistics import median

from scripts.config import load_league, load_settings
from scripts.db import get_connection
from scripts.exports import write_csv
from scripts.weighting import minutes
from scripts.weighting.weights import load_grid
from scripts.weighting.zscores import add_zscores, zscore_columns

log = logging.getLogger(__name__)

COUNTING = ("pts", "reb", "ast", "stl", "blk", "tov", "fg3m", "fga", "fta", "fg3a")
RAW_FIELDS = ("gp", "min", "mpg", "pts", "reb", "ast", "stl", "blk", "tov", "fg3m", "fg3a",
              "fga", "fgm", "fgp", "fta", "ftm", "ftp", "adp", "auction_cost", "auction_value")

FINAL_COLUMNS = [
    "phase", "season", "player_id", "player", "team", "positions",
    "gp", "mpg", "min", "pts", "reb", "ast", "stl", "blk", "tov",
    "fg3m", "fg3a", "fg3p", "fgm", "fga", "fgp", "ftm", "fta", "ftp",
    "adp", "auction_cost", "auction_value",
] + zscore_columns() + [
    "n_sources", "sources", "gp_coverage", "min_coverage", "stats_coverage", "fga_estimated",
    "weights_hash", "computed_at",
]
RANK_COLUMNS = {"rank_avg", "rank_tot"}
TEXT_COLUMNS = {"phase", "season", "player", "team", "positions", "sources", "weights_hash", "computed_at"}


def ensure_table(conn):
    cols = []
    for col in FINAL_COLUMNS:
        if col in TEXT_COLUMNS:
            cols.append(f"{col} TEXT")
        elif col in ("player_id", "n_sources", "fga_estimated") or col in RANK_COLUMNS:
            cols.append(f"{col} INTEGER")
        else:
            cols.append(f"{col} REAL")
    existing = [r[1] for r in conn.execute("PRAGMA table_info(final_projections)")]
    if existing and existing != FINAL_COLUMNS:
        conn.execute("DROP TABLE final_projections")  # table calculée : reconstruite à chaque run
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS final_projections (
            {", ".join(cols)},
            PRIMARY KEY (phase, season, player_id)
        )
    """)


def _load_slot(conn, slot):
    """{player_id: ligne} pour une source de la grille."""
    fields = ", ".join(("player_id", "canonical_name AS player", "team", "positions") + RAW_FIELDS)
    if slot["final"]:
        ensure_table(conn)
        sql = (f"SELECT player_id, player, team, positions, {', '.join(RAW_FIELDS)} "
               "FROM final_projections WHERE phase=? AND season=?")
        params = (slot["source"], slot["season"])
    else:
        sql = (f"SELECT {fields} FROM raw_projections r JOIN players p USING (player_id) "
               "WHERE r.source=? AND r.season=? AND r.stage=? AND r.player_id IS NOT NULL")
        params = (slot["source"], slot["season"], slot["stage"])
    cursor = conn.execute(sql, params)
    names = [d[0] for d in cursor.description]
    data = {}
    for values in cursor.fetchall():
        row = dict(zip(names, values))
        previous = data.get(row["player_id"])
        if previous is None or (row["gp"] or 0) > (previous["gp"] or 0):
            data[row["player_id"]] = row
    return data


def _weighted(pairs):
    """pairs = [(poids, valeur)] -> (moyenne pondérée renormalisée, somme des poids présents)."""
    pairs = [(w, v) for w, v in pairs if w > 0 and v is not None]
    total = sum(w for w, _ in pairs)
    if total <= 0:
        return None, 0.0
    return sum(w * v for w, v in pairs) / total, total


def _pick_text(entries, field):
    """Valeur la plus fréquente ; à égalité, celle de la source au plus gros poids STATS."""
    scores = {}
    for weight, row in entries:
        value = (row.get(field) or "").strip()
        if value:
            count, best = scores.get(value, (0, 0.0))
            scores[value] = (count + 1, max(best, weight))
    if not scores:
        return ""
    return max(scores.items(), key=lambda item: item[1])[0]


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _weighted_pct(entries, pct, att, final_att_pg):
    """FG% / FT% : moyenne des % de chaque source, pondérée par (poids de la grille x tentatives par match).

    - une source sans match (gp = 0) ou à 0 réussite sans tentatives connues (0/0, affiché « .000 » par
      les pages de stats réelles) ne donne pas de % : elle est ignorée au lieu de compter pour 0 % ;
    - le poids suit le volume : un 1/1 aux lancers sur 7 jours pèse bien moins qu'une projection à
      4 tentatives par match ; une source sans tentatives prend les tentatives finales du joueur.
    """
    rows = []
    for s, r in entries:
        value, games, attempts = r[pct], r["gp"], r[att]
        if value is None or games == 0 or s["weights"]["stats"][pct] <= 0:
            continue
        if not attempts and not value:
            continue
        per_game = attempts / games if attempts and games else None
        rows.append((s["weights"]["stats"][pct], value, per_game))
    known = [a for _, _, a in rows if a]
    if not known:
        return _weighted([(w, v) for w, v, _ in rows])[0]
    fill = final_att_pg or sum(known) / len(known)
    return _weighted([(w * (a or fill), v) for w, v, a in rows])[0]


def compute_player(entries):
    """entries = [(slot, row)] pour un joueur. Retourne le dict de projection finale."""
    gp, gp_cov = _weighted([(s["weights"]["gp"], r["gp"]) for s, r in entries])
    mpg_pairs = []
    for s, r in entries:
        mpg = r["mpg"]
        if mpg is None and r["min"] is not None and r["gp"]:
            mpg = r["min"] / r["gp"]
        mpg_pairs.append((s["weights"]["min"], mpg))
    mpg, min_cov = _weighted(mpg_pairs)

    out = {"gp": gp, "mpg": mpg, "gp_coverage": gp_cov, "min_coverage": min_cov}

    per_game = {}
    for cat in COUNTING:
        pairs = []
        for s, r in entries:
            total, games = r[cat], r["gp"]
            if total is None or (not games and not s.get("allow_per_game_without_gp")):
                continue
            value = total / games if games else total
            source_mpg = r["mpg"] or (r["min"] / games if r["min"] else None)
            if mpg:
                # production par minute (ou par match ramenée au MPG final si la source n'a pas de minutes)
                value = value / (source_mpg or mpg)
            pairs.append((s["weights"]["stats"][cat], value))
        rate, cov = _weighted(pairs)
        per_game[cat] = None if rate is None else (rate * mpg if mpg else rate)
        if cat == "pts":
            out["stats_coverage"] = cov

    for pct, att in (("fgp", "fga"), ("ftp", "fta")):
        out[pct] = _weighted_pct(entries, pct, att, per_game.get(att))

    games = gp or 0
    for cat in COUNTING:
        out[cat] = per_game[cat] * games if per_game[cat] is not None else None
    out["fga_estimated"] = 0
    out["_per_game"] = per_game
    return out


def _finalize_shooting(results):
    """FGM/FTM cohérents avec les % ; estimation des tentatives manquantes."""
    ratios = [r["fta"] / r["fga"] for r in results if r.get("fta") and r.get("fga")]
    ft_ratio = median(ratios) if ratios else 0.3
    ftps = [r["ftp"] for r in results if r.get("ftp")]
    default_ftp = median(ftps) if ftps else 0.78

    for r in results:
        if r["fga"] is None and r["pts"] is not None and r["fgp"]:
            ftp = r["ftp"] or default_ftp
            two_and_threes = r["pts"] - (r["fg3m"] or 0)
            r["fga"] = max(two_and_threes, 0) / (2 * r["fgp"] + ftp * ft_ratio)
            if r["fta"] is None:
                r["fta"] = ft_ratio * r["fga"]
            r["fga_estimated"] = 1
        elif r["fta"] is None and r["fga"] is not None:
            r["fta"] = ft_ratio * r["fga"]
            r["fga_estimated"] = 1
        r["fgm"] = r["fgp"] * r["fga"] if r["fgp"] is not None and r["fga"] is not None else None
        r["ftm"] = r["ftp"] * r["fta"] if r["ftp"] is not None and r["fta"] is not None else None
        r["fg3p"] = r["fg3m"] / r["fg3a"] if r.get("fg3a") and r.get("fg3m") is not None else None
        r["min"] = r["gp"] * r["mpg"] if r["gp"] is not None and r["mpg"] is not None else None


SEASON_OUT_ZEROED = COUNTING + ("gp", "mpg", "min", "fgm", "ftm", "fgp", "ftp", "fg3p")


def injury_phases(league):
    """Phases du reste de saison (gp = matchs restants) : lt et st (Optim | Season | LT / ST phase)."""
    season = league.get("season") or {}
    return (season.get("lt_phase") or "lt", season.get("st_phase") or "st")


def load_availability(phase, settings, league, today=None):
    """{clé du nom: date de retour | 'season'} (onglet injuries / ESPN) ; {} si indisponible."""
    from datetime import date

    from scripts import injuries

    try:
        return injuries.load(league, settings, today=today or date.today(),
                             timeout=settings.get("http", {}).get("timeout", 30))
    except Exception as exc:   # ne doit jamais bloquer la pondération
        log.warning("[Pondération %s] Blessures non prises en compte (%s).", phase, exc)
        return {}


def is_out_for_season(availability, player):
    from scripts import injuries
    from scripts.names import name_key

    return bool(availability) and availability.get(name_key(player or "")) == injuries.SEASON


def injury_gp_ratio(league):
    """Seuil Optim | Season | Injury GP ratio (1 par défaut)."""
    try:
        return float((league.get("season") or {}).get("injury_gp_ratio", 1.0) or 1.0)
    except (TypeError, ValueError):
        return 1.0


def filter_injured_gp(entries, left, ratio_max):
    """Blessé avec date de retour : sources du niveau GP qui projettent plus de matchs que `left` x seuil
    (blessure non prise en compte) retirées du niveau GP ; poids des autres renormalisés par compute_player.

    Retourne (entrées, [(code, matchs projetés)] exclues). Toutes exclues : entrées inchangées (le plafond
    d'apply_injuries s'applique)."""
    if left <= 0:
        return entries, []
    excluded, kept = [], 0
    for s, r in entries:
        if s["weights"]["gp"] > 0 and r.get("gp") is not None:
            if r["gp"] / left > ratio_max:
                excluded.append((s["code"], r["gp"]))
            else:
                kept += 1
    if not excluded or not kept:
        return entries, excluded
    codes = {c for c, _ in excluded}
    out = [(dict(s, weights=dict(s["weights"], gp=0.0)) if s["code"] in codes else s, r) for s, r in entries]
    return out, excluded


def apply_injuries(results, phase, season, settings, league, availability=None, games=None, today=None):
    """Blessés (onglet injuries / ESPN).

    - absent jusqu'à la fin de saison : joueur gardé, matchs, minutes, totaux et pourcentages à 0 ;
    - date de retour : matchs projetés limités aux matchs de son équipe après le retour, totaux réduits
      dans la même proportion (stats par match inchangées).
    Retourne le nombre de joueurs ajustés."""
    from scripts import injuries
    from scripts.names import name_key
    from scripts.season.schedule import games_by_team, load_games, team_code

    if availability is None:
        availability = load_availability(phase, settings, league, today)
    if not availability:
        return 0
    cache, changed, out = {}, [], []
    for r in results:
        back = availability.get(name_key(r.get("player") or ""))
        if back is None:
            continue
        if back == injuries.SEASON:
            for col in SEASON_OUT_ZEROED:
                r[col] = 0.0
            out.append(r["player"])
            continue
        if not r.get("gp"):
            continue
        if games is None:
            try:
                games = load_games(season, timeout=settings.get("http", {}).get("timeout", 30))
            except Exception as exc:
                log.warning("[Pondération %s] Calendrier NBA indisponible (%s) : dates de retour ignorées.",
                            phase, exc)
                games = []
        if not games:
            continue
        if back not in cache:
            cache[back] = games_by_team(games, back)
        left = cache[back].get(team_code(r.get("team") or ""), 0)
        if r["gp"] <= left:
            continue
        factor = left / r["gp"]
        changed.append(f"{r['player']} {r['gp']:.0f} -> {left}")
        r["gp"] = float(left)
        for cat in COUNTING + ("fgm", "ftm", "min"):
            if r.get(cat) is not None:
                r[cat] = r[cat] * factor
    if out:
        log.info("[Pondération %s] Blessures : %d joueur(s) absent(s) jusqu'à la fin de saison, projections "
                 "à 0 : %s", phase, len(out), ", ".join(out[:20]))
    if changed:
        log.info("[Pondération %s] Blessures : matchs projetés réduits pour %d joueur(s) : %s", phase,
                 len(changed), ", ".join(changed[:20]))
    return len(out) + len(changed)


def _round(value, digits):
    return None if value is None else round(value, digits)


def played_ids(conn, season):
    """Joueurs ayant joué au moins un match cette saison (stats réelles, étape sea)."""
    return {pid for (pid,) in conn.execute(
        "SELECT DISTINCT player_id FROM raw_projections WHERE season=? AND stage='sea' AND gp > 0 "
        "AND player_id IS NOT NULL", (season,))}


def drop_unreliable(loaded, phase, played, min_sources=2, keep=None):
    """Phase lt, avant la normalisation des minutes : retire les joueurs sans équipe NBA (FA) et ceux
    projetés par moins de `min_sources` sources sans avoir joué un match cette saison (projections
    isolées : joueur non signé, recrue projetée par une seule source...). Seule compte une source qui
    donne au joueur une équipe NBA (une ligne sans équipe, ex. RotoWire, ne confirme pas qu'il joue en
    NBA : Kadary Richmond, 9cat CLE 3 matchs + RotoWire sans équipe = 1 source). Dès son premier match, le
    joueur revient. Un joueur de la liste des blessés (`keep` : clés de nom) n'est jamais retiré : c'est un
    joueur NBA, gardé même absent jusqu'à la fin de saison. Modifie `loaded` en place ; retourne les noms
    retirés."""
    from scripts.names import name_key
    from scripts.season.schedule import team_code

    count, names = {}, {}           # count : sources qui donnent au joueur une équipe NBA
    for _, data in loaded:
        for pid, row in data.items():
            names.setdefault(pid, row.get("player") or "")
            count.setdefault(pid, 0)
            if team_code(row.get("team") or "") not in ("", "FA"):
                count[pid] += 1
    keep = keep or {}
    drop = {pid for pid in count if name_key(names[pid]) not in keep
            and (not count[pid] or (count[pid] < min_sources and pid not in played))}
    for _, data in loaded:
        for pid in drop & set(data):
            del data[pid]
    removed = sorted(names[pid] for pid in drop)
    if removed:
        log.info("[Pondération %s] %d joueur(s) retiré(s) (sans équipe NBA, ou moins de %d source(s) sans "
                 "match joué) : %s%s", phase, len(removed), min_sources, ", ".join(removed[:25]),
                 " ..." if len(removed) > 25 else "")
    return removed


def cap_games_left(results, phase, games, today=None):
    """Phase lt : GP plafonné aux matchs restants de l'équipe (totaux réduits dans la même proportion).
    Joueur sans équipe reconnue (FA...) : inchangé. Retourne le nombre de joueurs plafonnés."""
    from datetime import date

    from scripts.season.schedule import games_by_team, team_code

    left = games_by_team(games, today or date.today())
    capped = []
    for r in results:
        n = left.get(team_code(r.get("team") or ""))
        if not n or not r.get("gp") or r["gp"] <= n:
            continue
        factor = n / r["gp"]
        capped.append(f"{r['player']} {r['gp']:.0f} -> {n}")
        r["gp"] = float(n)
        for cat in COUNTING + ("fgm", "ftm", "min"):
            if r.get(cat) is not None:
                r[cat] = r[cat] * factor
    if capped:
        log.info("[Pondération %s] GP plafonné aux matchs restants de l'équipe pour %d joueur(s) : %s", phase,
                 len(capped), ", ".join(capped[:20]))
    return len(capped)


def _games(phase, season, settings):
    from scripts.season.schedule import load_games

    try:
        return load_games(season, timeout=settings.get("http", {}).get("timeout", 30))
    except Exception as exc:
        log.warning("[Pondération %s] Calendrier NBA indisponible (%s) : dates de retour ignorées.", phase, exc)
        return []


def _games_left(availability, games, entries):
    """Matchs de l'équipe du joueur après sa date de retour ; None si pas blessé (ou fin de saison)."""
    from datetime import date

    from scripts.names import name_key
    from scripts.season.schedule import games_by_team, team_code

    if not availability or not games:
        return None
    back = availability.get(name_key(entries[0][1]["player"] or ""))
    if not isinstance(back, date):
        return None
    team = _pick_text([(s["weights"]["stats"]["pts"], r) for s, r in entries], "team")
    return games_by_team(games, back).get(team_code(team), 0)


def _log_gp_filter(phase, notes, results):
    if not notes:
        return
    gp = {r["player"]: r.get("gp") for r in results}
    parts = []
    for player, (left, excluded, applied) in notes.items():
        detail = ", ".join(f"{code} {g:.0f}/{left}" for code, g in excluded)
        final = gp.get(player)
        tail = f"GP = {final:.0f}" if final is not None else "GP = ?"
        if not applied:
            tail += " (toutes les sources exclues : plafond des matchs restants)"
        parts.append(f"{player} : {detail} exclues ; {tail}")
    log.info("[Pondération %s] Blessures, sources du niveau GP exclues : %s", phase, " | ".join(parts[:20]))


def compute_phase(phase, settings=None, grid_path=None):
    """Calcule et enregistre les projections finales d'une phase. Retourne le nombre de joueurs."""
    settings = settings or load_settings()
    season = settings["active_season"]
    grid = load_grid(phase, season, grid_path)
    grid_hash = hashlib.sha1(json.dumps(grid["slots"], sort_keys=True, default=str).encode()).hexdigest()[:10]

    by_player, loaded = {}, []
    with get_connection() as conn:
        for slot in grid["slots"]:
            data = _load_slot(conn, slot)
            label = f"{slot['slot']} {slot['code']} ({slot['source']} {slot['season']} {slot['stage']})"
            if not data:
                log.warning("[Pondération %s] %s : aucune donnée, poids redistribués.", phase, label)
                continue
            log.info("[Pondération %s] %s : %d joueurs.", phase, label, len(data))
            loaded.append((slot, data))

    league = load_league()
    availability = load_availability(phase, settings, league) if phase in injury_phases(league) else {}
    is_lt = phase == ((league.get("season") or {}).get("lt_phase") or "lt")
    normalize = is_lt and minutes.params(league)["enabled"]

    games = None
    if is_lt or any(v != "season" for v in availability.values()):
        from scripts.injuries import season_end_as_out

        games = _games(phase, season, settings)
        # retour après le dernier match de la saison (ESPN : 2027-07-01...) = absent jusqu'à la fin de saison
        availability = season_end_as_out(availability, games)
    if is_lt:
        min_sources = int((league.get("season") or {}).get("min_sources_no_games", 2) or 0)
        if min_sources > 0:
            with get_connection() as conn:
                drop_unreliable(loaded, phase, played_ids(conn, season), min_sources, keep=availability)
    if normalize and games:
        with get_connection() as conn:
            real = minutes.real_mpg(conn, season, minutes.params(league)["min_real_games"])
        minutes.normalize(loaded, phase, league, games, availability, real)
    for slot, data in loaded:
        for player_id, row in data.items():
            by_player.setdefault(player_id, []).append((slot, row))
    ratio_max = injury_gp_ratio(league)
    gp_notes = {}

    results = []
    for player_id, entries in by_player.items():
        left = _games_left(availability, games, entries)
        if left is not None:
            filtered, excluded = filter_injured_gp(entries, left, ratio_max)
            if excluded:
                gp_notes[entries[0][1]["player"]] = (left, excluded, filtered is not entries)
            entries = filtered
        result = compute_player(entries)
        # sans match projeté : écarté, sauf blessé jusqu'à la fin de saison (gardé, tout à 0)
        if not result["gp"] and not is_out_for_season(availability, entries[0][1]["player"]):
            continue
        stats_entries = [(s["weights"]["stats"]["pts"], r) for s, r in entries]
        result.update(
            phase=phase, season=season, player_id=player_id,
            player=entries[0][1]["player"],
            team=_pick_text(stats_entries, "team"),
            positions=_pick_text(stats_entries, "positions"),
            adp=_mean([r["adp"] for _, r in entries if r["adp"] is None or r["adp"] < 900]),
            auction_cost=_mean([r["auction_cost"] for _, r in entries]),
            auction_value=_mean([r["auction_value"] for _, r in entries]),
            n_sources=len(entries),
            sources=",".join(s["code"] for s, _ in entries),
        )
        results.append(result)

    if not results:
        # aucune donnée pour cette phase (ex. projections ros pas encore publiées) : on garde le calcul
        # précédent plutôt que de vider la phase
        log.warning("[Pondération %s] Aucun joueur calculé : projections précédentes conservées.", phase)
        return 0
    if is_lt and games:
        cap_games_left(results, phase, games)
    if availability:
        apply_injuries(results, phase, season, settings, league, availability=availability, games=games or [])
        _log_gp_filter(phase, gp_notes, results)
    _finalize_shooting(results)
    add_zscores(results, league)
    computed_at = datetime.now().isoformat(timespec="seconds")
    rows = []
    for r in results:
        r["weights_hash"] = grid_hash
        r["computed_at"] = computed_at
        for col in FINAL_COLUMNS:
            if col in ("fgp", "ftp", "fg3p", "gp_coverage", "min_coverage", "stats_coverage"):
                r[col] = _round(r.get(col), 4)
            elif col.startswith("z_"):
                r[col] = _round(r.get(col), 3)
            elif col not in TEXT_COLUMNS and col not in RANK_COLUMNS and col not in ("player_id", "n_sources", "fga_estimated"):
                r[col] = _round(r.get(col), 2)
        rows.append(tuple(r.get(col) for col in FINAL_COLUMNS))

    with get_connection() as conn, conn:
        ensure_table(conn)
        conn.execute("DELETE FROM final_projections WHERE phase=? AND season=?", (phase, season))
        conn.executemany(
            f"INSERT INTO final_projections ({', '.join(FINAL_COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in FINAL_COLUMNS)})", rows,
        )

    full = sum(1 for r in results if (r.get("stats_coverage") or 0) > 0.999)
    estimated = sum(r["fga_estimated"] for r in results)
    log.info("[Pondération %s] %d joueurs calculés (%d couverts par toutes les sources, "
             "%d avec tentatives estimées).", phase, len(results), full, estimated)
    export_csv(phase, season, settings)
    return len(results)


def export_csv(phase, season, settings=None):
    """Écrit exports/final_<phase>_<saison>.csv (séparateur et décimale : Pipeline | Export)."""
    columns = [c for c in FINAL_COLUMNS if c not in ("weights_hash", "computed_at")]
    # tri selon le format de la ligue : H2H -> rang AVG, Rotisserie -> rang TOT
    order = "rank_tot" if str(load_league().get("format", "h2h")).lower() == "roto" else "rank_avg"
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(columns)} FROM final_projections WHERE phase=? AND season=? "
            f"ORDER BY {order}", (phase, season),
        ).fetchall()
    path = write_csv(f"final_{phase}_{season}.csv", columns, rows, settings)
    log.info("[Pondération %s] Export : %s", phase, path)
    return path


def run_phases(settings=None, phases=None):
    """Calcule les phases de l'étape active (settings["phases"]). Retourne True si tout est OK."""
    settings = settings or load_settings()
    phases = phases or settings.get("phases", {}).get(settings["active_stage"], [])
    ok = True
    for phase in phases:
        try:
            compute_phase(phase, settings)
        except FileNotFoundError as exc:
            log.warning("[Pondération %s] Grille absente (%s) : phase ignorée.", phase, exc)
        except ValueError as exc:
            log.error("[Pondération %s] %s", phase, exc)
            ok = False
    return ok
