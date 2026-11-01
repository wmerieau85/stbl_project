import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# les tests utilisent la configuration livrée (config/defaults.json), jamais la copie locale
os.environ["STBL_CONFIG"] = os.path.join(tempfile.gettempdir(), "stbl_tests_config_absent.json")

import pytest


@pytest.fixture(autouse=True)
def _no_injuries_network(monkeypatch, request):
    """Pas d'appel ESPN pendant les tests (liste de blessures vide), sauf tests de scripts/injuries."""
    if request.module.__name__.endswith("test_injuries"):
        return
    from scripts import injuries

    injuries._CACHE.clear()
    monkeypatch.setattr(injuries, "load", lambda *a, **k: {})
