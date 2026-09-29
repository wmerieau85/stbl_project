"""Rapprochement des joueurs entre sources via la table `players`.

Chaque ligne de raw_projections porte une clé normalisée (player_key). Le linker :
1. applique les alias de config/player_aliases.json (nom vu dans une source -> nom canonique) ;
   sans alias, le nom canonique d'un nouveau joueur est le nom lu, sans accents (Nikola Jokić ->
   Nikola Jokic), comme dans les onglets du classeur ;
2. retrouve ou crée le joueur correspondant dans `players` ;
3. renseigne raw_projections.player_id.
"""

import logging

from scripts.config import load_aliases
from scripts.db import get_connection
from scripts.names import clean_display_name, name_key, strip_accents

log = logging.getLogger(__name__)


def _alias_map():
    """Alias indexés par clé normalisée : insensibles aux accents, à la casse et aux suffixes."""
    return {name_key(src): clean_display_name(target) for src, target in load_aliases().items()}


def link_players(relink_all=False, season=None, stage=None):
    aliases = _alias_map()
    with get_connection() as conn, conn:
        if relink_all:
            conn.execute("UPDATE raw_projections SET player_id = NULL")

        unlinked = conn.execute(
            "SELECT player_key, MIN(player) FROM raw_projections "
            "WHERE player_id IS NULL GROUP BY player_key"
        ).fetchall()
        known = dict(conn.execute("SELECT name_key, player_id FROM players").fetchall())
        created = 0

        for raw_key, display_name in unlinked:
            canonical = aliases.get(raw_key) or strip_accents(clean_display_name(display_name))
            canonical_key = name_key(canonical)
            player_id = known.get(canonical_key)
            if player_id is None:
                cursor = conn.execute(
                    "INSERT INTO players (canonical_name, name_key) VALUES (?, ?)",
                    (canonical, canonical_key),
                )
                player_id = cursor.lastrowid
                known[canonical_key] = player_id
                created += 1
            conn.execute(
                "UPDATE raw_projections SET player_id = ? WHERE player_key = ? AND player_id IS NULL",
                (player_id, raw_key),
            )

    log.info("[Linker] %d clé(s) traitée(s), %d nouveau(x) joueur(s).", len(unlinked), created)
    if season and stage:
        report_coverage(season, stage)


def report_coverage(season, stage):
    """Affiche combien de joueurs sont couverts par chaque source et par toutes."""
    scope = "FROM raw_projections WHERE season = ? AND stage = ?"
    params = (season, stage)
    with get_connection() as conn:
        n_sources = conn.execute(f"SELECT COUNT(DISTINCT source) {scope}", params).fetchone()[0]
        if n_sources < 2:
            return
        per_source = conn.execute(
            f"SELECT source, COUNT(DISTINCT player_id) {scope} GROUP BY source", params
        ).fetchall()
        in_all = conn.execute(
            f"SELECT COUNT(*) FROM (SELECT player_id {scope} "
            "GROUP BY player_id HAVING COUNT(DISTINCT source) = ?)",
            params + (n_sources,),
        ).fetchone()[0]
    detail = ", ".join(f"{src} : {n}" for src, n in per_source)
    log.info("[Linker] %s %s -> %s ; présents dans toutes les sources : %d.", season, stage, detail, in_all)


def unmatched_players(source, season, stage):
    """Joueurs d'une source absents des autres (même saison/étape) : utile pour compléter les alias."""
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT r.player, r.team FROM raw_projections r
            WHERE r.source = ? AND r.season = ? AND r.stage = ? AND r.player_id NOT IN (
                SELECT player_id FROM raw_projections
                WHERE source <> ? AND season = ? AND stage = ? AND player_id IS NOT NULL
            )
            ORDER BY r.pts DESC
            """,
            (source, season, stage, source, season, stage),
        ).fetchall()


if __name__ == "__main__":
    from scripts.config import load_settings

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    active = load_settings()
    link_players(season=active["active_season"], stage=active["active_stage"])
