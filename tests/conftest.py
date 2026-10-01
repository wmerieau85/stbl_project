import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# les tests utilisent la configuration livrée (config/defaults.json), jamais la copie locale
os.environ["STBL_CONFIG"] = os.path.join(tempfile.gettempdir(), "stbl_tests_config_absent.json")
