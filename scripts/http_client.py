"""Client HTTP partagé : timeout, nouvelles tentatives et pause entre requêtes."""

import logging
import re
import time

import cloudscraper

log = logging.getLogger(__name__)


def safe_url(url):
    """URL sans clé d'API (pour les messages)."""
    return re.sub(r"(apikey|api_key|key|token)=[^&]+", r"\1=***", url or "", flags=re.I)


class HttpClient:
    def __init__(self, timeout=30, retries=2, pause_seconds=1.5):
        self.timeout = timeout
        self.retries = retries
        self.pause_seconds = pause_seconds
        self._session = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "desktop": True}
        )
        self._last_request = 0.0

    @classmethod
    def from_settings(cls, settings):
        return cls(**settings.get("http", {}))

    def _wait_politely(self):
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.pause_seconds:
            time.sleep(self.pause_seconds - elapsed)

    def get_text(self, url):
        """Retourne le HTML de la page, ou None après échec de toutes les tentatives."""
        for attempt in range(1, self.retries + 2):
            self._wait_politely()
            try:
                response = self._session.get(url, timeout=self.timeout)
                self._last_request = time.monotonic()
                if response.status_code == 200:
                    return response.text
                log.warning("HTTP %s sur %s (tentative %d)", response.status_code, safe_url(url), attempt)
            except Exception as exc:  # erreurs réseau, timeouts, Cloudflare...
                self._last_request = time.monotonic()
                log.warning("Erreur réseau sur %s (tentative %d) : %s", safe_url(url), attempt, safe_url(str(exc)))
        log.error("Échec définitif : %s", safe_url(url))
        return None
