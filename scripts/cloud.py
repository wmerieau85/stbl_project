"""Base de données et fichiers CSV partagés sur Google Drive (lancements depuis le menu du classeur).

Dossier Drive (Sheets | Settings | Drive folder ID), partagé en Éditeur avec le compte de service :
    database.sqlite          base de référence (déposée une première fois à la main)
    lineupexperts/ ...       un sous-dossier par source CSV, du nom du dossier local d'import
                             (data/imports/lineupexperts -> lineupexperts) : fichiers .csv copiés en local

Lancement GitHub (scripts.run) : base et CSV téléchargés au début, base renvoyée à la fin si elle a changé.
Lancement sur le PC avec Sheets | Settings | Drive sync = oui : même chose autour de chaque commande
(python main.py, python -m scripts.season ...). La base Drive fait foi.

Un compte de service n'a pas d'espace de stockage : il ne peut que mettre à jour un fichier existant,
d'où le dépôt initial de database.sqlite à la main.
"""

import hashlib
import logging
import os
import sys
from datetime import datetime

from scripts.config import BASE_DIR, load_league, load_settings

log = logging.getLogger("stbl.cloud")

API = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER = "application/vnd.google-apps.folder"
DB_NAME = "database.sqlite"
RUNNER_ENV = "STBL_CLOUD_RUNNER"      # positionné par scripts.run : les commandes ne resynchronisent pas


class CloudError(RuntimeError):
    pass


def folder_id(league=None):
    league = league if league is not None else load_league()
    return str((league.get("google_sheets") or {}).get("drive_folder_id") or "").strip()


def local_sync_enabled(league=None):
    """Synchronisation autour des commandes lancées sur le PC (pas dans un lancement scripts.run)."""
    if os.environ.get(RUNNER_ENV):
        return False
    try:
        league = league if league is not None else load_league()
    except Exception:   # configuration illisible : la commande dira pourquoi
        return False
    return bool((league.get("google_sheets") or {}).get("drive_sync")) and bool(folder_id(league))


def session(settings=None):
    """Session HTTP authentifiée avec le compte de service (portée Drive)."""
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2.service_account import Credentials

    from scripts.sheets import _service_account_path

    creds = Credentials.from_service_account_file(_service_account_path(settings or load_settings()),
                                                  scopes=["https://www.googleapis.com/auth/drive"])
    return AuthorizedSession(creds)


def _md5(path):
    if not os.path.exists(path):
        return None
    digest = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check(response, what):
    if response.status_code >= 400:
        try:
            detail = response.json().get("error", {}).get("message", "")
        except ValueError:
            detail = response.text[:200]
        if "has not been used" in detail or "is disabled" in detail:
            detail += " (activez « Google Drive API » dans la console Google Cloud du compte de service)"
        raise CloudError(f"Google Drive, {what} : {response.status_code} {detail}")
    return response


class Drive:
    def __init__(self, folder, http=None, settings=None):
        if not folder:
            raise CloudError("Dossier Drive absent : renseignez Sheets | Settings | Drive folder ID dans l'onglet settings.")
        self.folder = folder
        self.http = http or session(settings)

    def children(self, parent, folders=None):
        query = f"'{parent}' in parents and trashed = false"
        if folders is True:
            query += f" and mimeType = '{FOLDER}'"
        elif folders is False:
            query += f" and mimeType != '{FOLDER}'"
        params = {"q": query, "fields": "nextPageToken, files(id, name, mimeType, md5Checksum, modifiedTime)",
                  "pageSize": 1000, "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
        out = []
        while True:
            data = _check(self.http.get(API, params=params, timeout=60), "lecture du dossier").json()
            out += data.get("files", [])
            if not data.get("nextPageToken"):
                return out
            params["pageToken"] = data["nextPageToken"]

    def find(self, name, parent=None):
        return next((f for f in self.children(parent or self.folder, folders=False) if f["name"] == name), None)

    def download(self, file, path):
        response = _check(self.http.get(f"{API}/{file['id']}", params={"alt": "media", "supportsAllDrives": "true"},
                                        timeout=300), f"téléchargement de {file['name']}")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".download"
        with open(tmp, "wb") as fh:
            fh.write(response.content)
        os.replace(tmp, path)
        stamp = _timestamp(file.get("modifiedTime"))
        if stamp:
            os.utime(path, (stamp, stamp))

    def upload(self, file, path):
        with open(path, "rb") as fh:
            data = fh.read()
        _check(self.http.patch(f"{UPLOAD}/{file['id']}", params={"uploadType": "media", "supportsAllDrives": "true"},
                               data=data, headers={"Content-Type": "application/octet-stream"}, timeout=300),
               f"envoi de {file['name']}")


def _timestamp(text):
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _db_path():
    from scripts import db

    return db.DB_PATH


def pull_db(drive, path=None):
    """Télécharge la base Drive si elle diffère de la base locale. Renvoie son empreinte md5."""
    path = path or _db_path()
    remote = drive.find(DB_NAME)
    if remote is None:
        raise CloudError(f"{DB_NAME} absent du dossier Drive : déposez-y une première fois la base de votre PC "
                         f"({os.path.relpath(path, BASE_DIR)}).")
    if remote.get("md5Checksum") and remote["md5Checksum"] == _md5(path):
        log.info("[Drive] Base locale déjà à jour.")
    else:
        drive.download(remote, path)
        log.info("[Drive] Base téléchargée (%s, %.1f Mo).", remote.get("modifiedTime", ""), os.path.getsize(path) / 1e6)
    return _md5(path)


def push_db(drive, before=None, path=None):
    """Renvoie la base sur Drive si elle a changé depuis pull_db. Renvoie True si envoyée."""
    path = path or _db_path()
    current = _md5(path)
    if current is None or current == before:
        log.info("[Drive] Base inchangée : rien à renvoyer.")
        return False
    remote = drive.find(DB_NAME)
    if remote is None:
        raise CloudError(f"{DB_NAME} absent du dossier Drive.")
    drive.upload(remote, path)
    log.info("[Drive] Base renvoyée (%.1f Mo).", os.path.getsize(path) / 1e6)
    return True


def import_dirs(settings=None):
    """{nom du sous-dossier Drive: dossier local} pour les sources lues dans des fichiers."""
    from scripts.config import load_sources_config

    out = {}
    for conf in load_sources_config().values():
        folder = conf.get("import_dir")
        if folder:
            local = folder if os.path.isabs(folder) else os.path.join(BASE_DIR, folder)
            out[os.path.basename(os.path.normpath(folder))] = local
    return out


def pull_imports(drive, dirs=None):
    """Copie en local les .csv des sous-dossiers Drive des sources. Renvoie le nombre de fichiers copiés."""
    dirs = dirs if dirs is not None else import_dirs()
    count = 0
    for sub in drive.children(drive.folder, folders=True):
        local = dirs.get(sub["name"])
        if local is None:
            continue
        for f in drive.children(sub["id"], folders=False):
            if not f["name"].lower().endswith(".csv"):
                continue
            path = os.path.join(local, f["name"])
            if f.get("md5Checksum") and f["md5Checksum"] == _md5(path):
                continue
            drive.download(f, path)
            count += 1
            log.info("[Drive] %s/%s copié.", sub["name"], f["name"])
    return count


def run_synced(main, argv=None, write=True):
    """Lance main(argv) entre téléchargement et renvoi de la base, si Drive sync = oui sur ce PC."""
    if not local_sync_enabled():
        return main(argv) if argv is not None else main()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    try:
        drive = Drive(folder_id())
        before = pull_db(drive)
        pull_imports(drive)
    except Exception as exc:
        log.error("[Drive] Synchronisation impossible (%s) : commande non lancée, pour ne pas diverger de la "
                  "base Drive. Mettez Sheets | Settings | Drive sync à non pour travailler hors ligne.", exc)
        return 1
    rc = main(argv) if argv is not None else main()
    if write:
        try:
            push_db(drive, before)
        except Exception as exc:
            log.error("[Drive] Base non renvoyée (%s) : relancez la commande ou déposez-la à la main.", exc)
            return rc or 1
    return rc


if __name__ == "__main__":   # python -m scripts.cloud pull | push : synchronisation manuelle
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action not in ("pull", "push"):
        print("usage : python -m scripts.cloud pull | push")
        sys.exit(2)
    d = Drive(folder_id())
    if action == "pull":
        pull_db(d)
        pull_imports(d)
    else:
        push_db(d)
    sys.exit(0)
