import json
import os
import pandas as pd

# Remplace 'Sans nom 1.csv' par le nom exact de ton fichier CSV s'il est différent
csv_filename = "Sans nom 1.csv"
output_json_path = os.path.join("config", "player_aliases.json")

# Charger le fichier CSV
df = pd.read_csv(csv_filename)

# Récupérer les deux colonnes (player_base et players name)
col_base = df.columns[0]
col_target = df.columns[1]

# Créer le dictionnaire avec l'intégralité des lignes
mapping = dict(zip(df[col_base].astype(str), df[col_target].astype(str)))

# Assurer que le dossier config existe
os.makedirs("config", exist_ok=True)

# Sauvegarder le fichier JSON complet
with open(output_json_path, "w", encoding="utf-8") as f:
    json.dump(mapping, f, ensure_ascii=False, indent=2)

print(
    f"✅ Succès ! {len(mapping)} lignes ont été exportées dans '{output_json_path}'."
)