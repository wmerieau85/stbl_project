"""Lecture / écriture Google Sheets via un compte de service (gspread).

Le fichier JSON du compte de service est indiqué dans config/settings.json :
    "google": {"service_account_file": "credentials/service_account.json"}
(chemin relatif à la racine du projet ; le dossier credentials/ est ignoré par git).
Le classeur doit être partagé avec l'adresse e-mail du compte de service (droit Éditeur).
"""

import logging
import os

from scripts.config import BASE_DIR, load_settings

log = logging.getLogger(__name__)


class SheetsError(RuntimeError):
    pass


def _service_account_path(settings=None):
    settings = settings or load_settings()
    path = settings.get("google", {}).get("service_account_file") or ""
    if path and not os.path.isabs(path):
        path = os.path.join(BASE_DIR, path)
    if not path or not os.path.exists(path):
        raise SheetsError(
            f"Fichier du compte de service introuvable : '{path}'. "
            "Renseignez google.service_account_file dans config/settings.json."
        )
    return path


def open_spreadsheet(spreadsheet_id, settings=None):
    if not spreadsheet_id:
        raise SheetsError("Identifiant de classeur vide : renseignez google_sheets dans config/league.json.")
    try:
        import gspread
    except ImportError as exc:  # pragma: no cover
        raise SheetsError("Module gspread absent : pip install -r requirements.txt") from exc
    client = gspread.service_account(filename=_service_account_path(settings))
    return client.open_by_key(spreadsheet_id)


def read_range(spreadsheet, tab, cell_range):
    """Valeurs affichées d'une plage (liste de lignes)."""
    return spreadsheet.worksheet(tab).get(cell_range)


def write_tab(spreadsheet, tab, rows):
    """Remplace tout le contenu d'un onglet (créé s'il n'existe pas)."""
    import gspread

    width = max((len(r) for r in rows), default=1)
    rows = [list(r) + [""] * (width - len(r)) for r in rows]
    try:
        ws = spreadsheet.worksheet(tab)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=tab, rows=max(len(rows) + 10, 100), cols=max(width, 26))
    if ws.row_count < len(rows) or ws.col_count < width:
        ws.resize(rows=max(ws.row_count, len(rows)), cols=max(ws.col_count, width))
    ws.clear()
    ws.update(values=[[_cell(v) for v in r] for r in rows], range_name="A1", value_input_option="RAW")
    log.info("[Sheets] Onglet '%s' mis à jour (%d lignes).", tab, len(rows))


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, float):
        return round(value, 6)
    return value
