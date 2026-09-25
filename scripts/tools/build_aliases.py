"""Génère config/player_aliases.json à partir d'un CSV à deux colonnes.

Colonne 1 : nom tel qu'il apparaît dans une source ; colonne 2 : nom canonique.
Remplace l'ancien temp.py.

    python -m scripts.tools.build_aliases mon_fichier.csv
    python -m scripts.tools.build_aliases mon_fichier.csv --replace   # écrase au lieu de fusionner
"""

import argparse
import json
import os

import pandas as pd

from scripts.config import ALIASES_PATH, load_aliases


def build_aliases(csv_path, output_path=ALIASES_PATH, replace=False):
    df = pd.read_csv(csv_path, sep=None, engine="python", dtype=str).dropna()
    if df.shape[1] < 2:
        raise ValueError("Le CSV doit contenir au moins deux colonnes (nom source, nom canonique).")

    new_aliases = {
        src.strip(): target.strip()
        for src, target in zip(df.iloc[:, 0], df.iloc[:, 1])
        if src.strip() and target.strip() and src.strip() != target.strip()
    }
    aliases = {} if replace else load_aliases()
    aliases.update(new_aliases)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(aliases.items())), f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"{len(new_aliases)} alias lus, {len(aliases)} au total dans {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv_path")
    parser.add_argument("--output", default=ALIASES_PATH)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    build_aliases(args.csv_path, args.output, args.replace)
