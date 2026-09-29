"""Client de l'API Yahoo Fantasy Sports (OAuth 2.0, lecture seule).

Fichiers locaux (dossier credentials/, jamais versionné) :
- credentials/yahoo_app.json   : {"client_id": "...", "client_secret": "..."} (à créer à la main)
- credentials/yahoo_token.json : jeton d'accès, créé par `python -m scripts.yahoo auth`
  puis renouvelé automatiquement.

Chemins réglables dans config/settings.json > "yahoo".
"""

import base64
import json
import logging
import os
import time
import urllib.parse
import webbrowser

import requests

from scripts.config import BASE_DIR, load_settings

log = logging.getLogger(__name__)

AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
API_URL = "https://fantasysports.yahooapis.com/fantasy/v2"
DEFAULT_REDIRECT = "https://localhost:8080"


class YahooError(RuntimeError):
    pass


def _path(value):
    return value if os.path.isabs(value) else os.path.join(BASE_DIR, value)


class YahooClient:
    def __init__(self, settings=None):
        settings = settings or load_settings()
        conf = settings.get("yahoo", {})
        self.app_path = _path(conf.get("app_file", "credentials/yahoo_app.json"))
        self.token_path = _path(conf.get("token_file", "credentials/yahoo_token.json"))
        self.redirect_uri = conf.get("redirect_uri", DEFAULT_REDIRECT)
        self.timeout = settings.get("http", {}).get("timeout", 30)
        self.session = requests.Session()
        self._app = None
        self._token = None

    # --- identifiants ----------------------------------------------------------------
    @property
    def app(self):
        if self._app is None:
            if not os.path.exists(self.app_path):
                raise YahooError(
                    f"Fichier {self.app_path} introuvable. Créez-le avec le Client ID et le Client Secret "
                    'de votre application Yahoo : {"client_id": "...", "client_secret": "..."}'
                )
            with open(self.app_path, encoding="utf-8") as fh:
                self._app = json.load(fh)
            if not self._app.get("client_id") or not self._app.get("client_secret"):
                raise YahooError(f"{self.app_path} : client_id et client_secret sont obligatoires.")
        return self._app

    def _basic_auth(self):
        raw = f"{self.app['client_id']}:{self.app['client_secret']}".encode()
        return {"Authorization": "Basic " + base64.b64encode(raw).decode()}

    def _save_token(self, data):
        data = dict(data)
        data["expires_at"] = time.time() + int(data.get("expires_in", 3600)) - 60
        os.makedirs(os.path.dirname(self.token_path), exist_ok=True)
        with open(self.token_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        self._token = data

    def _request_token(self, payload):
        resp = self.session.post(TOKEN_URL, data=payload, headers=self._basic_auth(), timeout=self.timeout)
        if resp.status_code != 200:
            raise YahooError(f"Yahoo a refusé la demande de jeton ({resp.status_code}) : {resp.text[:300]}")
        self._save_token(resp.json())

    # --- autorisation (une seule fois) ------------------------------------------------
    def authorize(self, open_browser=True):
        params = {"client_id": self.app["client_id"], "redirect_uri": self.redirect_uri,
                  "response_type": "code", "language": "fr-fr"}
        url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
        print("1. Connectez-vous à Yahoo et cliquez sur « Autoriser » :")
        print(f"   {url}")
        if open_browser:
            webbrowser.open(url)
        print("2. Le navigateur arrive ensuite sur une page https://localhost:8080/?code=... qui ne s'affiche pas :")
        print("   c'est normal. Copiez l'adresse complète de cette page (ou seulement le code).")
        answer = input("Adresse ou code : ").strip()
        code = answer
        if "code=" in answer:
            code = urllib.parse.parse_qs(urllib.parse.urlparse(answer).query).get("code", [""])[0]
        if not code:
            raise YahooError("Aucun code d'autorisation fourni.")
        self._request_token({"grant_type": "authorization_code", "redirect_uri": self.redirect_uri, "code": code})
        print(f"Autorisation enregistrée dans {self.token_path}")

    @property
    def token(self):
        if self._token is None:
            if not os.path.exists(self.token_path):
                raise YahooError("Pas encore d'autorisation Yahoo : lancez python -m scripts.yahoo auth")
            with open(self.token_path, encoding="utf-8") as fh:
                self._token = json.load(fh)
        if time.time() >= float(self._token.get("expires_at", 0)):
            log.debug("Renouvellement du jeton Yahoo.")
            self._request_token({"grant_type": "refresh_token", "redirect_uri": self.redirect_uri,
                                 "refresh_token": self._token["refresh_token"]})
        return self._token["access_token"]

    # --- requêtes ---------------------------------------------------------------------
    def get(self, resource, retry=True):
        """GET sur /fantasy/v2/<resource> ; renvoie fantasy_content (JSON)."""
        url = f"{API_URL}/{resource.lstrip('/')}"
        sep = "&" if "?" in url else "?"
        resp = self.session.get(f"{url}{sep}format=json", timeout=self.timeout,
                                headers={"Authorization": f"Bearer {self.token}"})
        if resp.status_code == 401 and retry:
            self._token["expires_at"] = 0  # jeton expiré côté Yahoo : on renouvelle et on réessaie
            return self.get(resource, retry=False)
        if resp.status_code == 403:
            raise YahooError("Accès refusé par Yahoo (403). Si l'application vient d'être créée, l'accès à "
                             f"l'API Fantasy est peut-être en attente de validation. Détail : {resp.text[:300]}")
        if resp.status_code != 200:
            raise YahooError(f"Erreur Yahoo {resp.status_code} sur {resource} : {resp.text[:300]}")
        return resp.json().get("fantasy_content", {})
