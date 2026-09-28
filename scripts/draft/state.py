"""État de la draft : ordre snake, keepers, choix saisis, joueurs libres."""

import csv
import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class Pick:
    overall: int          # 0 = premier choix de la draft
    round: int            # 1..rounds
    pick: int             # 1..teams (rang dans le tour)
    team: str
    name: str             # nom saisi
    player: object = None  # Player reconnu (None si inconnu)


@dataclass
class DraftState:
    order: list
    rounds: int
    my_team: str
    keepers: dict                         # manager -> [Player]
    picks: dict = field(default_factory=dict)   # overall -> Pick
    unknown: list = field(default_factory=list)  # noms non reconnus
    duplicates: list = field(default_factory=list)

    @property
    def teams(self):
        return len(self.order)

    @property
    def total_picks(self):
        return self.teams * self.rounds

    def team_at(self, overall):
        rnd, pos = divmod(overall, self.teams)
        return self.order[pos] if rnd % 2 == 0 else self.order[self.teams - 1 - pos]

    def slot(self, overall):
        rnd, pos = divmod(overall, self.teams)
        return rnd + 1, pos + 1

    @property
    def current(self):
        """Premier choix non encore saisi (None si draft terminée)."""
        for overall in range(self.total_picks):
            if overall not in self.picks:
                return overall
        return None

    def my_picks_from(self, start):
        return [o for o in range(start, self.total_picks) if self.team_at(o) == self.my_team and o not in self.picks]

    def rosters(self):
        """manager -> liste de Player (keepers + choix reconnus)."""
        result = {team: list(self.keepers.get(team, [])) for team in self.order}
        for pk in sorted(self.picks.values(), key=lambda p: p.overall):
            if pk.player is not None:
                result.setdefault(pk.team, []).append(pk.player)
        return result

    def taken(self):
        ids = {p.idx for players in self.keepers.values() for p in players}
        ids |= {pk.player.idx for pk in self.picks.values() if pk.player is not None}
        return ids


def build_state(league, pool, pick_rows):
    """pick_rows : [(tour, choix_dans_le_tour, nom)] tels que saisis dans le classeur."""
    draft = league["draft"]
    order = list(draft.get("order") or [])
    teams = int(league["teams"])
    if len(order) != teams:
        raise ValueError(f"league.json : draft.order contient {len(order)} managers, {teams} attendus.")
    my_team = draft.get("my_team") or ""
    if my_team not in order:
        raise ValueError(f"league.json : draft.my_team '{my_team}' absent de draft.order.")
    if str(draft.get("type", "snake")).lower() != "snake":
        raise ValueError("Seule la draft snake est gérée pour l'instant.")

    unknown, seen, duplicates = [], set(), []
    keepers = {}
    for team, names in (draft.get("keepers") or {}).items():
        if team not in order:
            log.warning("Keepers : manager '%s' absent de draft.order, ignoré.", team)
            continue
        for name in names or []:
            player = pool.find(name)
            if player is None:
                unknown.append(f"{name} (keeper {team})")
                continue
            if player.idx in seen:
                duplicates.append(player.name)
            seen.add(player.idx)
            keepers.setdefault(team, []).append(player)

    state = DraftState(order=order, rounds=int(draft.get("rounds", 12)), my_team=my_team, keepers=keepers)
    for rnd, pick, name in pick_rows:
        name = (name or "").strip()
        if not name:
            continue
        try:
            rnd, pick = int(float(rnd)), int(float(pick))
        except (TypeError, ValueError):
            continue
        if not (1 <= rnd <= state.rounds and 1 <= pick <= teams):
            continue
        overall = (rnd - 1) * teams + (pick - 1)
        player = pool.find(name)
        if player is None:
            unknown.append(f"{name} (tour {rnd}, choix {pick})")
        elif player.idx in seen:
            duplicates.append(player.name)
        else:
            seen.add(player.idx)
        state.picks[overall] = Pick(overall, rnd, pick, state.team_at(overall), name, player)
    state.unknown, state.duplicates = unknown, duplicates
    for label in unknown:
        log.warning("Nom non reconnu : %s (ajoutez un alias dans config/player_aliases.json)", label)
    for name in duplicates:
        log.warning("Joueur saisi deux fois : %s", name)
    return state


# --- Lecture des choix hors ligne (tests, mock drafts) ------------------------------------

def picks_from_xlsx(path, tab="draft_res"):
    import openpyxl

    ws = openpyxl.load_workbook(path, data_only=True, read_only=True)[tab]
    rows = []
    for values in ws.iter_rows(min_row=2, max_col=4, values_only=True):
        rnd, pick, _key, name = (list(values) + [None] * 4)[:4]
        rows.append((rnd, pick, name))
    return rows


def picks_from_csv(path):
    """CSV avec colonnes round;pick;player (séparateur ; ou ,)."""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(2048)
        fh.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=";,")
        return [(r.get("round"), r.get("pick"), r.get("player")) for r in csv.DictReader(fh, dialect=dialect)]


def picks_from_sheet_rows(rows):
    """Lignes brutes de la plage draft_res!A2:D : tour, choix, clé, joueur."""
    out = []
    for r in rows:
        r = list(r) + [""] * 4
        out.append((r[0], r[1], r[3]))
    return out
