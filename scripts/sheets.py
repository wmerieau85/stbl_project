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
    key_file = _service_account_path(settings)
    email = _client_email(key_file)
    client = gspread.service_account(filename=key_file)
    try:
        return client.open_by_key(spreadsheet_id)
    except PermissionError as exc:
        detail = _api_message(exc.__cause__)
        if "has not been used" in detail or "is disabled" in detail:
            raise SheetsError(
                "L'API Google Sheets n'est pas activée pour le projet du compte de service. "
                "Activez « Google Sheets API » et « Google Drive API » dans la console Google Cloud "
                f"(API et services > Bibliothèque), puis relancez. Détail : {detail}"
            ) from exc
        raise SheetsError(
            f"Accès refusé au classeur {spreadsheet_id}. Partagez-le (bouton Partager, rôle Éditeur) "
            f"avec le compte de service : {email}"
        ) from exc
    except gspread.SpreadsheetNotFound as exc:
        raise SheetsError(
            f"Classeur {spreadsheet_id} introuvable : vérifiez google_sheets.draft_spreadsheet_id "
            f"et qu'il est partagé avec {email}"
        ) from exc
    except gspread.exceptions.APIError as exc:
        raise SheetsError(f"Erreur de l'API Google Sheets : {_api_message(exc)}") from exc


def _client_email(key_file):
    import json

    try:
        with open(key_file, encoding="utf-8") as fh:
            return json.load(fh).get("client_email") or "(client_email absent du fichier)"
    except (OSError, ValueError):
        return "(fichier du compte de service illisible)"


def _api_message(exc):
    try:
        return exc.response.json()["error"]["message"]
    except Exception:
        return str(exc or "")


def service_account_email(settings=None):
    return _client_email(_service_account_path(settings))


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


def write_block(spreadsheet, tab, rows, first_col="A", last_col=None):
    """Écrit un bloc de colonnes (ex. A:BH) sans toucher aux autres colonnes de l'onglet.

    Le bloc est vidé sur toute sa hauteur avant l'écriture ; les formules situées à droite
    (ou à gauche) du bloc sont conservées.
    """
    from gspread.utils import a1_to_rowcol, rowcol_to_a1

    width = max((len(r) for r in rows), default=1)
    rows = [[_cell(v) for v in list(r) + [""] * (width - len(r))] for r in rows]
    try:
        ws = spreadsheet.worksheet(tab)
    except Exception:  # onglet absent : créé
        ws = spreadsheet.add_worksheet(title=tab, rows=max(len(rows), 100), cols=width + 2)
        log.info("[Sheets] Onglet '%s' créé.", tab)
    start_col = a1_to_rowcol(f"{first_col}1")[1]
    end_col = start_col + width - 1
    if last_col:
        end_col = max(end_col, a1_to_rowcol(f"{last_col}1")[1])
    if ws.row_count < len(rows) or ws.col_count < end_col:
        ws.resize(rows=max(ws.row_count, len(rows)), cols=max(ws.col_count, end_col))
    last = rowcol_to_a1(ws.row_count, end_col)
    ws.batch_clear([f"{first_col}1:{last}"])
    ws.update(values=rows, range_name=f"{first_col}1", value_input_option="RAW")
    log.info("[Sheets] Onglet '%s', colonnes %s:%s mises à jour (%d lignes).", tab, first_col,
             rowcol_to_a1(1, end_col).rstrip("1"), len(rows))
    return ws
