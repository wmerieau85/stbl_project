"""Calcul des projections finales pondérées.

Modèle à 3 niveaux, chacun pondéré par sa ligne de la grille :

    Total d'une stat = GP  x  MPG  x  production par minute
                       (GP)   (MIN)   (STATS)

- GP  : moyenne pondérée des matchs joués.
- MPG : moyenne pondérée des minutes par match.
- STATS : moyenne pondérée de la production par minute de chaque source.
  Une source sans minutes (ex. DraftKick) est ramenée à la minute avec le MPG final :
  sa moyenne par match compte alors telle quelle.
- FG% / FT% : moyenne pondérée des pourcentages (toutes les sources qui les donnent) ;
  les tentatives (FGA, FTA) viennent des sources qui les fournissent ;
  FGM = FG% x FGA et FTM = FT% x FTA, donc cohérents avec les pourcentages.
  Sans aucune source de tentatives : FGA estimé par
  FGA = (PTS - 3PM) / (2 x FG% + FT% x r), FTA = r x FGA, r = ratio FTA/FGA médian.

Si une source ne couvre pas un joueur (ou une stat), les poids des sources présentes
sont renormalisés ; les colonnes *_coverage indiquent la part du poids réellement présente.
"""

import hashlib
import json
import logging
import os
from datetime import datetime
from statistics import median

from scripts.config import EXPORTS_DIR, load_league, load_settings
from scripts.db import get_connection
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
            if total is None or not games:
                continue
            value = total / games
            source_mpg = r["mpg"] or (r["min"] / games if r["min"] else None)
            if mpg:
                # production par minute (ou par match ramenée au MPG final si la source n'a pas de minutes)
                value = value / (source_mpg or mpg)
            pairs.append((s["weights"]["stats"][cat], value))
        rate, cov = _weighted(pairs)
        per_game[cat] = None if rate is None else (rate * mpg if mpg else rate)
        if cat == "pts":
            out["stats_coverage"] = cov

    for pct in ("fgp", "ftp"):
        out[pct], _ = _weighted([(s["weights"]["stats"][pct], r[pct]) for s, r in entries])

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


def _round(value, digits):
    return None if value is None else round(value, digits)


def compute_phase(phase, settings=None, grid_path=None):
    """Calcule et enregistre les projections finales d'une phase. Retourne le nombre de joueurs."""
    settings = settings or load_settings()
    season = settings["active_season"]
    grid = load_grid(phase, season, grid_path)
    grid_hash = hashlib.sha1(json.dumps(grid["slots"], sort_keys=True, default=str).encode()).hexdigest()[:10]

    by_player = {}
    with get_connection() as conn:
        for slot in grid["slots"]:
            data = _load_slot(conn, slot)
            label = f"{slot['slot']} {slot['code']} ({slot['source']} {slot['season']} {slot['stage']})"
            if not data:
                log.warning("[Pondération %s] %s : aucune donnée, poids redistribués.", phase, label)
                continue
            log.info("[Pondération %s] %s : %d joueurs.", phase, label, len(data))
            for player_id, row in data.items():
                by_player.setdefault(player_id, []).append((slot, row))

    results = []
    for player_id, entries in by_player.items():
        result = compute_player(entries)
        if not result["gp"]:
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

    _finalize_shooting(results)
    add_zscores(results, load_league())
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
    """Écrit exports/final_<phase>_<saison>.csv (séparateur et décimale de settings.json)."""
    settings = settings or load_settings()
    export = settings.get("export", {})
    delimiter = export.get("delimiter", ";")
    decimal = export.get("decimal", ",")
    os.makedirs(EXPORTS_DIR, exist_ok=True)
    path = os.path.join(EXPORTS_DIR, f"final_{phase}_{season}.csv")
    columns = [c for c in FINAL_COLUMNS if c not in ("weights_hash", "computed_at")]
    # tri selon le format de la ligue : H2H -> rang AVG, Rotisserie -> rang TOT
    order = "rank_tot" if str(load_league().get("format", "h2h")).lower() == "roto" else "rank_avg"
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(columns)} FROM final_projections WHERE phase=? AND season=? "
            f"ORDER BY {order}", (phase, season),
        ).fetchall()

    def fmt(value):
        if value is None:
            return ""
        if isinstance(value, float):
            text = repr(value)
            return text.replace(".", decimal) if decimal != "." else text
        text = str(value)
        return f'"{text}"' if delimiter in text or '"' in text else text

    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(delimiter.join(columns) + "\r\n")
        for row in rows:
            f.write(delimiter.join(fmt(v) for v in row) + "\r\n")
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
