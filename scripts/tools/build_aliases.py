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


def _read_csv(csv_path):
    # Les exports Excel/LibreOffice sous Windows sont souvent en cp1252 plutôt qu'en UTF-8
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return pd.read_csv(csv_path, sep=None, engine="python", dtype=str, encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Encodage non reconnu pour {csv_path}")


def build_aliases(csv_path, output_path=ALIASES_PATH, replace=False, skip_identity=False):
    df = _read_csv(csv_path).dropna()
    if df.shape[1] < 2:
        raise ValueError("Le CSV doit contenir au moins deux colonnes (nom source, nom canonique).")

    new_aliases = {
        src.strip(): target.strip()
        for src, target in zip(df.iloc[:, 0], df.iloc[:, 1])
        if src.strip() and target.strip() and not (skip_identity and src.strip() == target.strip())
    }
    corrupted = sorted(name for name in new_aliases if "?" in name)
    if corrupted:
        print(f"Attention : {len(corrupted)} nom(s) contiennent '?' (caractères perdus à l'export du CSV).")
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
    parser.add_argument("--replace", action="store_true", help="écrase le fichier au lieu de fusionner")
    parser.add_argument("--skip-identity", action="store_true", help="ignore les lignes où les deux noms sont identiques")
    args = parser.parse_args()
    build_aliases(args.csv_path, args.output, args.replace, args.skip_identity)
