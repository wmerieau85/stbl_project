"""Recalcule les projections finales sans relancer les imports.

    python -m scripts.weighting                 # phases de l'étape active
    python -m scripts.weighting --phase draft   # une phase précise
    python -m scripts.weighting --phase draft --grid C:/chemin/grille.csv
"""

import argparse
import logging
import sys

from scripts.config import load_settings
from scripts.db import init_db
from scripts.weighting.engine import compute_phase, run_phases

parser = argparse.ArgumentParser(description="Projections finales pondérées")
parser.add_argument("--phase", help="draft, lt, st... (sinon phases de l'étape active)")
parser.add_argument("--season", help="ex. 2026-27")
parser.add_argument("--grid", help="grille CSV à utiliser à la place de celle de l'onglet settings")
args = parser.parse_args()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
settings = load_settings()
if args.season:
    settings["active_season"] = args.season
init_db()
if args.phase:
    compute_phase(args.phase, settings, grid_path=args.grid)
    sys.exit(0)
sys.exit(0 if run_phases(settings) else 1)
