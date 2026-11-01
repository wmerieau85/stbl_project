"""Blessures : liste ESPN (statut + date de retour estimée) et onglet « injuries » du classeur, qui fait foi.

Source : https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries (données de
https://www.espn.com/nba/injuries). À chaque calcul (pondération lt, Saison > Projections / Moves),
la liste ESPN est relue et l'onglet est réécrit :

    Player | NBA | Status | ESPN return | Override | Return used | Updated | Comment

- Override (saisi à la main, conservé d'une mise à jour à l'autre, ligne gardée même si ESPN ne liste
  plus le joueur) : une date (2027-03-02 ou 02/03/2027), « season » / « saison » (absent jusqu'à la fin
  de saison), ou « ok » (blessure ignorée). Un joueur absent d'ESPN peut être ajouté avec son Override.
- Sans Override : date de retour ESPN des joueurs « Out » ; « Day-To-Day » ignoré (retour imminent),
  « Out » sans date : ignoré (signalé), statut contenant « season » : fin de saison.
- Return used : date retenue ; aucun match avant cette date (ni en ST ni en LT), matchs projetés
  ramenés au calendrier restant après le retour (phase lt et simulation de saison).
ESPN indisponible : la dernière version de l'onglet est utilisée telle quelle.

Réglages : Optim | Season | Injuries (espn = ESPN + onglet, sheet = onglet seul, off) et
Sheets | Tab | Injuries (nom de l'onglet).
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime

import requests

from scripts.names import clean_display_name, name_key

log = logging.getLogger(__name__)

ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
HEADER = ["Player", "NBA", "Status", "ESPN return", "Override", "Return used", "Updated", "Comment"]
SEASON = "season"


@dataclass
class Injury:
    player: str
    nba_team: str = ""
    status: str = ""
    espn_return: str = ""
    override: str = ""
    updated: str = ""
    comment: str = ""
    return_used: str = ""        # AAAA-MM-JJ, « season » ou ""


def _parse_date(text):
    text = str(text or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10] if fmt == "%Y-%m-%d" else text, fmt).date()
        except ValueError:
            continue
    return None


def _override_value(text):
    """'season', 'ok', AAAA-MM-JJ ou None (vide / illisible)."""
    raw = str(text or "").strip().lower()
    if not raw:
        return None
    if raw in ("season", "saison", "fin de saison", "ofs"):
        return SEASON
    if raw in ("ok", "healthy", "aucune", "non", "no"):
        return "ok"
    d = _parse_date(raw)
    if d:
        return d.isoformat()
    log.warning("[Blessures] Override illisible « %s » : ignoré (date, season ou ok attendu).", text)
    return None


def resolve(inj):
    """Date retenue : Override, sinon règle ESPN."""
    o = _override_value(inj.override)
    if o == "ok":
        return ""
    if o:
        return o
    status = inj.status.lower()
    if "season" in status:
        return SEASON
    if status.startswith("out"):
        d = _parse_date(inj.espn_return)
        if d:
            return d.isoformat()
        log.info("[Blessures] %s : « %s » sans date de retour ESPN, ignoré (Override possible).",
                 inj.player, inj.status)
    return ""


def fetch_espn(timeout=30):
    data = requests.get(ESPN_URL, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"}).json()
    out = []
    for team in data.get("injuries") or []:
        abbr = ""
        for item in team.get("injuries") or []:
            athlete = item.get("athlete") or {}
            abbr = ((athlete.get("team") or {}).get("abbreviation") or abbr or "")
            name = clean_display_name(athlete.get("displayName") or "")
            if not name:
                continue
            out.append(Injury(player=name, nba_team=abbr, status=item.get("status") or "",
                              espn_return=((item.get("details") or {}).get("returnDate") or "")[:10],
                              updated=(item.get("date") or "")[:10],
                              comment=(item.get("shortComment") or "").strip()))
    return out


def from_rows(rows):
    """Lignes de l'onglet -> [Injury] (en-tête ignoré)."""
    out = []
    for r in rows[1:] if rows else []:
        r = list(r) + [""] * (len(HEADER) - len(r))
        if not str(r[0]).strip():
            continue
        out.append(Injury(player=str(r[0]).strip(), nba_team=str(r[1]).strip(), status=str(r[2]).strip(),
                          espn_return=str(r[3]).strip(), override=str(r[4]).strip(), updated=str(r[6]).strip(),
                          comment=str(r[7]).strip()))
    return out


def merge(espn, sheet):
    """Liste ESPN + Override de l'onglet (+ lignes manuelles avec Override absentes d'ESPN)."""
    overrides = {name_key(i.player): i.override for i in sheet if i.override}
    seen, out = set(), []
    for inj in espn:
        key = name_key(inj.player)
        if key in seen:
            continue
        seen.add(key)
        inj.override = overrides.get(key, "")
        out.append(inj)
    for inj in sheet:
        key = name_key(inj.player)
        if inj.override and key not in seen:
            seen.add(key)
            out.append(inj)
    for inj in out:
        inj.return_used = resolve(inj)
    return out


def to_rows(injuries):
    order = sorted(injuries, key=lambda i: (i.return_used == "", i.return_used != SEASON,
                                            i.return_used or "9999", i.player))
    return [HEADER] + [[i.player, i.nba_team, i.status, i.espn_return, i.override, i.return_used, i.updated,
                        i.comment] for i in order]


def availability(injuries, today):
    """{clé du nom: date de retour (date) ou 'season'} des joueurs indisponibles après `today`."""
    out = {}
    for inj in injuries:
        used = inj.return_used
        if used == SEASON:
            out[name_key(inj.player)] = SEASON
        elif used:
            d = _parse_date(used)
            if d and d > today:
                out[name_key(inj.player)] = d
    return out


def _open_book(league, settings):
    gs = league.get("google_sheets", {})
    if not gs.get("draft_spreadsheet_id"):
        return None
    try:
        from scripts.sheets import open_spreadsheet

        return open_spreadsheet(gs["draft_spreadsheet_id"], settings)
    except Exception as exc:  # pas d'accès au classeur (local sans clé...) : ESPN seul
        log.warning("[Blessures] Classeur inaccessible (%s) : liste ESPN sans Override.", exc)
        return None


_CACHE = {}


def load(league, settings=None, book=None, today=None, write=True, timeout=30):
    """Liste des blessures en vigueur (ESPN + onglet) ; réécrit l'onglet si possible.

    Retourne {clé du nom: date | 'season'} ; une seule lecture par processus.
    """
    today = today or date.today()
    mode = str((league.get("season") or {}).get("injuries", "espn") or "espn").strip().lower()
    if mode in ("off", "non", "no", "false", "0"):
        return {}
    key = (mode, today)
    if key in _CACHE:
        return _CACHE[key]
    tab = league.get("google_sheets", {}).get("injuries_tab") or "injuries"
    if book is None and write:
        book = _open_book(league, settings or {})
    sheet = []
    if book is not None:
        try:
            from scripts.sheets import read_range

            sheet = from_rows(read_range(book, tab, "A1:H3000"))
        except Exception:  # onglet absent : créé plus bas
            sheet = []
    espn = None
    if mode == "espn":
        try:
            espn = fetch_espn(timeout)
        except (requests.RequestException, ValueError) as exc:
            log.warning("[Blessures] ESPN indisponible (%s) : dernière version de l'onglet utilisée.", exc)
    if espn is None:
        injuries = sheet
        for inj in injuries:
            inj.return_used = resolve(inj)
    else:
        injuries = merge(espn, sheet)
        if book is not None and write:
            from scripts.sheets import write_tab

            write_tab(book, tab, to_rows(injuries))
    out = availability(injuries, today)
    log.info("[Blessures] %d joueurs listés, %d indisponibles après le %s (%d jusqu'à la fin de saison).",
             len(injuries), len(out), today.isoformat(), sum(1 for v in out.values() if v == SEASON))
    _CACHE[key] = out
    return out


def available_from(availability_map, player, today):
    """Date à partir de laquelle le joueur peut jouer (None = fin de saison, today = disponible)."""
    v = availability_map.get(name_key(player)) if availability_map else None
    if v == SEASON:
        return None
    if isinstance(v, date) and v > today:
        return v
    return today


