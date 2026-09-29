"""Joueurs draftables : projections finales (final_projections) prêtes pour l'assistant."""

import logging
from dataclasses import dataclass, field

import numpy as np

from scripts.config import load_aliases, load_settings
from scripts.db import get_connection
from scripts.names import clean_display_name, name_key, has_lost_chars, match_lost_chars

log = logging.getLogger(__name__)

# Statistiques additionnées pour le classement roto (FG% et FT% recalculés à partir des tentatives)
STATS = ("fgm", "fga", "ftm", "fta", "fg3m", "reb", "ast", "stl", "blk", "tov", "pts")
CATEGORY_LABELS = {
    "fgp": "FG%", "fg3m": "3PM", "ftp": "FT%", "reb": "REB", "ast": "AST",
    "stl": "STL", "blk": "BLK", "tov": "TO", "pts": "PTS",
}
POSITION_GROUPS = {"G": {"PG", "SG", "G"}, "F": {"SF", "PF", "F"}, "C": {"C"}}


@dataclass
class Player:
    idx: int
    player_id: int
    name: str
    team: str
    positions: str
    gp: float
    per_game: np.ndarray            # STATS par match
    value_avg: float                # somme des z-scores AVG (ordre de titularisation)
    value_tot: float                # somme des z-scores TOT (valeur roto)
    rank_tot: int
    adp: float | None
    market: float                   # rang de marché estimé (ADP, sinon rang TOT)
    groups: set = field(default_factory=set)


class Pool:
    def __init__(self, players):
        self.players = players
        self._by_key = {}
        for p in players:
            self._by_key.setdefault(name_key(p.name), p)
        self._aliases = {name_key(src): name_key(dst) for src, dst in load_aliases().items()}

    def __len__(self):
        return len(self.players)

    def find(self, name):
        """Joueur correspondant à un nom saisi (accents, casse, suffixes et alias tolérés)."""
        key = name_key(name)
        if not key:
            return None
        key = self._aliases.get(key, key)
        player = self._by_key.get(key)
        if player is None and has_lost_chars(key):  # « Nikola Joki? » (accent perdu)
            match = match_lost_chars(key, self._by_key)
            player = self._by_key.get(match) if match else None
        if player is None:  # dernier recours : "Nom Prénom" inversé ou initiale
            parts = key.split()
            if len(parts) >= 2:
                player = self._by_key.get(" ".join(parts[1:] + parts[:1]))
        return player


def _groups(positions):
    tokens = {t.strip().upper() for t in (positions or "").replace("/", ",").split(",") if t.strip()}
    return {g for g, members in POSITION_GROUPS.items() if tokens & members}


def load_pool(season=None, phase=None):
    settings = load_settings()
    season = season or settings["active_season"]
    phase = phase or settings["active_stage"]
    cols = ["player_id", "player", "team", "positions", "gp", "adp", "z_sum_avg", "z_sum_tot", "rank_tot"] + list(STATS)
    with get_connection() as conn:
        cursor = conn.execute(
            f"SELECT {', '.join(cols)} FROM final_projections WHERE phase=? AND season=? ORDER BY rank_tot",
            (phase, season),
        )
        rows = [dict(zip(cols, r)) for r in cursor.fetchall()]
    if not rows:
        raise RuntimeError(f"Aucune projection finale pour {phase} {season} : lancez d'abord python main.py")

    players = []
    for i, r in enumerate(rows):
        gp = float(r["gp"] or 0)
        per_game = np.array([(float(r[s] or 0) / gp) if gp else 0.0 for s in STATS])
        adp = float(r["adp"]) if r["adp"] is not None else None
        players.append(Player(
            idx=i, player_id=r["player_id"], name=clean_display_name(r["player"]), team=r["team"] or "",
            positions=r["positions"] or "", gp=gp, per_game=per_game,
            value_avg=float(r["z_sum_avg"] or 0), value_tot=float(r["z_sum_tot"] or 0),
            rank_tot=int(r["rank_tot"] or 9999), adp=adp, market=0.0, groups=_groups(r["positions"]),
        ))
    _market_ranks(players)
    log.info("[Draft] %d joueurs chargés (%s %s), %d avec ADP.", len(players), phase, season,
             sum(p.adp is not None for p in players))
    return Pool(players)


def _market_ranks(players):
    """Rang de marché : ADP quand il existe, sinon interpolé à partir du rang TOT.

    Les joueurs sans ADP sont placés là où leur rang TOT les situerait parmi les joueurs
    qui ont un ADP (un joueur sans ADP classé 150e en TOT reçoit l'ADP du ~150e joueur).
    """
    with_adp = sorted((p for p in players if p.adp is not None), key=lambda p: p.rank_tot)
    ranks = np.array([p.rank_tot for p in with_adp], dtype=float)
    adps = np.maximum.accumulate(np.array([p.adp for p in with_adp], dtype=float)) if with_adp else None
    for p in players:
        if p.adp is not None:
            p.market = p.adp
        elif with_adp:
            p.market = float(np.interp(p.rank_tot, ranks, adps, right=adps[-1] + (p.rank_tot - ranks[-1])))
        else:
            p.market = float(p.rank_tot)
