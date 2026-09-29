"""Lecture des données de ligue Yahoo : ligues, équipes, réglages, résultats de draft.

Le JSON Yahoo est très imbriqué : les collections sont des dicts {"0": ..., "1": ..., "count": n}
et les objets des listes de petits dicts à fusionner. Les fonctions _collection / _merge
aplanissent tout ça.
"""

import logging

log = logging.getLogger(__name__)


def _merge(obj):
    """Fusionne les listes de dicts Yahoo ([{a:1},{b:2},[{c:3}]]) en un seul dict."""
    out = {}
    if isinstance(obj, dict):
        return obj
    for item in obj or []:
        if isinstance(item, list):
            out.update(_merge(item))
        elif isinstance(item, dict):
            out.update(item)
    return out


def _collection(container, name):
    """{"0": {name: ...}, "1": {...}, "count": n} -> [objet, ...]."""
    if not isinstance(container, dict):
        return []
    items = []
    for key in sorted((k for k in container if str(k).isdigit()), key=int):
        value = container[key].get(name) if isinstance(container[key], dict) else None
        if value is not None:
            items.append(value)
    return items


def _league_parts(content):
    league = content.get("league")
    if isinstance(league, list):
        return _merge(league[:1]), league[1] if len(league) > 1 else {}
    return league or {}, {}


def game_key(client, code="nba"):
    game = client.get(f"game/{code}").get("game")
    return str(_merge(game).get("game_key"))


def league_key(client, league_id, code="nba"):
    league_id = str(league_id).strip()
    if ".l." in league_id:
        return league_id
    return f"{game_key(client, code)}.l.{league_id}"


def my_leagues(client, code="nba"):
    """Ligues NBA du compte connecté (saison en cours) : [{league_key, league_id, name, season, ...}]."""
    content = client.get(f"users;use_login=1/games;game_codes={code}/leagues")
    users = content.get("users", {})
    leagues = []
    for user in _collection(users, "user"):
        for game in _collection(_merge(user).get("games", {}), "game"):
            game_info = _merge(game[:1]) if isinstance(game, list) else game
            game_rest = game[1] if isinstance(game, list) and len(game) > 1 else {}
            for league in _collection(game_rest.get("leagues", {}), "league"):
                info = _merge(league[:1]) if isinstance(league, list) else league
                leagues.append({
                    "league_key": info.get("league_key"), "league_id": info.get("league_id"),
                    "name": info.get("name"), "season": info.get("season") or game_info.get("season"),
                    "num_teams": info.get("num_teams"), "draft_status": info.get("draft_status"),
                    "scoring_type": info.get("scoring_type"), "url": info.get("url"),
                })
    return leagues


def league_settings(client, key):
    info, rest = _league_parts(client.get(f"league/{key}/settings"))
    settings = _merge(rest.get("settings", [{}]))
    roster = {}
    for pos in settings.get("roster_positions", []) or []:
        p = pos.get("roster_position", {})
        roster[p.get("position")] = int(p.get("count", 0))
    cats = []
    for stat in (settings.get("stat_categories", {}) or {}).get("stats", []) or []:
        s = stat.get("stat", {})
        if not s.get("is_only_display_stat"):
            cats.append(s.get("display_name") or s.get("name"))
    return {
        "name": info.get("name"), "season": info.get("season"), "num_teams": info.get("num_teams"),
        "scoring_type": info.get("scoring_type"), "draft_status": info.get("draft_status"),
        "draft_type": settings.get("draft_type"), "is_auction": settings.get("is_auction_draft"),
        "uses_keepers": settings.get("uses_keepers") or settings.get("keeper_count"),
        "max_games_played": settings.get("max_games_played"), "roster": roster, "categories": cats,
    }


def teams(client, key):
    """[{team_key, team_id, name, manager}] de la ligue."""
    _, rest = _league_parts(client.get(f"league/{key}/teams"))
    out = []
    for team in _collection(rest.get("teams", {}), "team"):
        info = _merge(team)
        managers = info.get("managers") or []
        nick = ""
        if managers:
            first = managers[0].get("manager", {}) if isinstance(managers, list) else {}
            nick = first.get("nickname", "")
        out.append({"team_key": info.get("team_key"), "team_id": info.get("team_id"),
                    "name": info.get("name"), "manager": nick})
    return out


def draft_results(client, key, cache=None):
    """[{pick, round, team_key, player_key, player, positions, nba_team}] triés par pick,
    y compris les choix pas encore faits (player vide) : ils donnent l'ordre réel de la draft."""
    _, rest = _league_parts(client.get(f"league/{key}/draftresults"))
    picks = []
    for item in _collection(rest.get("draft_results", {}), "draft_result"):
        d = _merge(item)
        if not d.get("pick"):
            continue
        picks.append({"pick": int(d["pick"]), "round": int(d["round"]), "team_key": d.get("team_key"),
                      "player_key": d.get("player_key") or ""})
    cache = {} if cache is None else cache
    missing = [p["player_key"] for p in picks if p["player_key"] and p["player_key"] not in cache]
    if missing:
        cache.update(player_names(client, key, missing))
    empty = {"player": "", "positions": "", "nba_team": ""}
    for p in picks:
        p.update(cache.get(p["player_key"], empty) if p["player_key"] else empty)
    return sorted(picks, key=lambda p: p["pick"])


def player_names(client, key, player_keys):
    """{player_key: {player, positions, nba_team}} par paquets de 25 (limite Yahoo)."""
    out = {}
    keys = list(dict.fromkeys(player_keys))
    for i in range(0, len(keys), 25):
        chunk = ",".join(keys[i:i + 25])
        _, rest = _league_parts(client.get(f"league/{key}/players;player_keys={chunk}"))
        for player in _collection(rest.get("players", {}), "player"):
            info = _merge(player)
            name = info.get("name", {})
            out[info.get("player_key")] = {
                "player": name.get("full", "") if isinstance(name, dict) else str(name),
                "positions": info.get("display_position", ""),
                "nba_team": (info.get("editorial_team_abbr") or "").upper(),
            }
    return out
