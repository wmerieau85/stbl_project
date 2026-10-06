"""Pré-classement (O-Rank) et ADP Yahoo des joueurs, lus dans le contexte de ma ligue.

API : league/<clé>/players;sort=OR;start=N;count=25/draft_analysis
- l'ordre de la réponse (sort=OR) donne le rang Yahoo, celui de la salle de draft ;
- draft_analysis donne l'ADP Yahoo (average_pick, tous les drafts Yahoo), le tour moyen et le
  % de drafts où le joueur est pris.

Les joueurs sont rattachés aux joueurs de la base (players) par leur nom (alias de l'onglet
players compris) et enregistrés dans la table yahoo_rankings.
"""

import logging
from datetime import datetime

from scripts.config import load_aliases
from scripts.db import get_connection
from scripts.names import clean_display_name, has_lost_chars, match_lost_chars, name_key, strip_accents
from scripts.yahoo.league import _collection, _league_parts, _merge

log = logging.getLogger(__name__)

PAGE = 25
COLUMNS = ("season", "rank", "player_key", "player", "nba_team", "positions", "adp", "avg_round",
           "pct_drafted", "avg_cost", "player_id", "fetched_at")


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_players(content, start=0):
    """Réponse Yahoo (une page) -> [{rank, player_key, player, nba_team, positions, adp, ...}]."""
    _, rest = _league_parts(content)
    rows = []
    for i, player in enumerate(_collection(rest.get("players", {}), "player")):
        info = _merge(player)
        name = info.get("name", {})
        draft = _merge(info.get("draft_analysis") or [])
        adp = _num(draft.get("average_pick"))
        if adp is None:
            adp = _num(draft.get("preseason_average_pick"))
        pct = _num(draft.get("percent_drafted"))
        if pct is None:
            pct = _num(draft.get("preseason_percent_drafted"))
        rows.append({
            "rank": start + i + 1,
            "player_key": info.get("player_key"),
            "player": name.get("full", "") if isinstance(name, dict) else str(name or ""),
            "nba_team": (info.get("editorial_team_abbr") or "").upper(),
            "positions": info.get("display_position", ""),
            "adp": adp,
            "avg_round": _num(draft.get("average_round")),
            "pct_drafted": pct,
            "avg_cost": _num(draft.get("average_cost")),
        })
    return rows


def fetch_rankings(client, key, count=300):
    """Les `count` premiers joueurs du classement Yahoo (O-Rank), par pages de 25."""
    rows = []
    for start in range(0, max(1, int(count)), PAGE):
        page = parse_players(client.get(f"league/{key}/players;sort=OR;start={start};count={PAGE}/draft_analysis"),
                             start)
        rows += page
        if len(page) < PAGE:
            break
    return rows[:count]


def ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS yahoo_rankings (
            season TEXT, rank INTEGER, player_key TEXT, player TEXT, nba_team TEXT, positions TEXT,
            adp REAL, avg_round REAL, pct_drafted REAL, avg_cost REAL, player_id INTEGER, fetched_at TEXT,
            PRIMARY KEY (season, player_key)
        )""")


def link(rows, conn):
    """Ajoute player_id (joueur de la base) à chaque ligne ; renvoie les noms non trouvés."""
    known = dict(conn.execute("SELECT name_key, player_id FROM players").fetchall())
    aliases = {name_key(src): clean_display_name(dst) for src, dst in load_aliases().items()}
    missing = []
    for r in rows:
        key = name_key(r["player"])
        key = name_key(aliases[key]) if key in aliases else name_key(strip_accents(clean_display_name(r["player"])))
        player_id = known.get(key)
        if player_id is None and has_lost_chars(key):
            match = match_lost_chars(key, known)
            player_id = known.get(match) if match else None
        r["player_id"] = player_id
        if player_id is None:
            missing.append(r["player"])
    return missing


def save_rankings(rows, season):
    """Remplace le classement Yahoo de la saison. Renvoie la liste des joueurs non rattachés."""
    fetched = datetime.now().isoformat(timespec="seconds")
    with get_connection() as conn, conn:
        ensure_table(conn)
        missing = link(rows, conn)
        conn.execute("DELETE FROM yahoo_rankings WHERE season=?", (season,))
        conn.executemany(
            f"INSERT OR REPLACE INTO yahoo_rankings ({', '.join(COLUMNS)}) VALUES ({', '.join('?' for _ in COLUMNS)})",
            [tuple(dict(r, season=season, fetched_at=fetched).get(c) for c in COLUMNS) for r in rows],
        )
    log.info("[Yahoo] Classement enregistré : %d joueurs (%d avec ADP), %d non rattachés à la base.",
             len(rows), sum(r["adp"] is not None for r in rows), len(missing))
    if missing:
        log.warning("[Yahoo] Joueurs du classement absents des projections (alias à ajouter dans l'onglet players "
                    "s'il s'agit d'un nom différent) : %s", ", ".join(missing[:30]) + (" ..." if len(missing) > 30 else ""))
    return missing


def rankings_by_player(season):
    """{player_id: {rank, adp, ...}} pour la saison (vide si le classement n'a jamais été récupéré)."""
    with get_connection() as conn:
        ensure_table(conn)
        cursor = conn.execute("SELECT * FROM yahoo_rankings WHERE season=? AND player_id IS NOT NULL ORDER BY rank",
                              (season,))
        names = [d[0] for d in cursor.description]
        out = {}
        for values in cursor.fetchall():
            row = dict(zip(names, values))
            out.setdefault(row["player_id"], row)
    return out


def update(settings, league, count=None):
    """Récupère et enregistre le classement Yahoo de la ligue configurée. Renvoie le nombre de joueurs."""
    from scripts.yahoo.client import YahooClient, YahooError
    from scripts.yahoo.league import league_key

    league_id = str((league.get("yahoo") or {}).get("league_id") or "").strip()
    if not league_id:
        raise YahooError("ID de ligue Yahoo absent (Settings | League | League ID).")
    client = YahooClient(settings)
    key = league_key(client, league_id)
    count = count or int((league.get("yahoo") or {}).get("rankings_count") or 300)
    rows = fetch_rankings(client, key, count)
    if not rows:
        raise YahooError("Classement Yahoo vide.")
    save_rankings(rows, settings["active_season"])
    return len(rows)
