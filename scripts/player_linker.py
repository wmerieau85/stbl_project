"""Rapprochement des joueurs entre sources via la table `players`.

Chaque ligne de raw_projections porte une clé normalisée (player_key). Le linker :
1. applique les alias (onglet players) (nom vu dans une source -> nom canonique) ;
   sans alias, le nom canonique d'un nouveau joueur est le nom lu, sans accents (Nikola Jokić ->
   Nikola Jokic), comme dans les onglets du classeur ;
2. retrouve ou crée le joueur correspondant dans `players` ;
3. renseigne raw_projections.player_id.

Un nom dont un caractère a été perdu à l'encodage (« Nikola Joki? ») est rattaché au joueur
connu qui correspond, s'il est unique. repair_players() corrige les joueurs déjà enregistrés :
accents retirés des noms, doublons « à caractère perdu » fusionnés.
"""

import logging

from scripts.config import load_aliases
from scripts.db import get_connection
from scripts.names import clean_display_name, has_lost_chars, match_lost_chars, name_key, strip_accents

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
            if player_id is None and has_lost_chars(canonical_key):
                match = match_lost_chars(canonical_key, known)
                if match:
                    player_id = known[match]
                else:
                    log.warning("[Linker] Nom mal encodé sans correspondance : %s (ajoutez un alias).", display_name)
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
    repair_players()
    if season and stage:
        report_coverage(season, stage)


def repair_players():
    """Noms enregistrés : accents retirés, doublons à caractère perdu fusionnés. Idempotent."""
    renamed = merged = 0
    with get_connection() as conn, conn:
        players = conn.execute("SELECT player_id, canonical_name, name_key FROM players").fetchall()
        keys = {key: pid for pid, _, key in players}
        for pid, name, key in players:
            if has_lost_chars(key):
                match = match_lost_chars(key, keys)
                if match:
                    conn.execute("UPDATE raw_projections SET player_id = ? WHERE player_id = ?", (keys[match], pid))
                    conn.execute("DELETE FROM players WHERE player_id = ?", (pid,))
                    merged += 1
                continue
            plain = strip_accents(name)
            if plain != name:
                conn.execute("UPDATE players SET canonical_name = ? WHERE player_id = ?", (plain, pid))
                renamed += 1
    if renamed or merged:
        log.info("[Linker] Noms corrigés : %d sans accents, %d doublon(s) mal encodé(s) fusionné(s). "
                 "Relancez la pondération pour mettre à jour les projections finales.", renamed, merged)
    return renamed, merged


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
