import json
import os
import pandas as pd


def load_mappings():
  """Charge le dictionnaire de correspondance des colonnes depuis config/mappings.json."""
  config_path = os.path.join(
      os.path.dirname(__file__), '..', 'config', 'mappings.json'
  )
  # L'utilisation de 'utf-8-sig' supprime les caractères BOM invisibles en début de fichier
  with open(config_path, 'r', encoding='utf-8-sig') as f:
    return json.load(f)


COLUMN_MAPPINGS = load_mappings()


def standardize_dataframe(df, source_name):
  """Applique la standardisation des noms de colonnes et des types de données pour une source donnée."""
  if source_name not in COLUMN_MAPPINGS:
    raise ValueError(
        f"Source '{source_name}' non trouvée dans config/mappings.json"
    )

  mapping = COLUMN_MAPPINGS[source_name]

  # Renommage des colonnes selon le mapping JSON
  df = df.rename(columns=mapping)

  # Conservation uniquement des colonnes standardisées présentes
  standard_cols = [col for col in mapping.values() if col in df.columns]
  df = df[standard_cols].copy()

  # Nettoyage des valeurs numériques (remplace virgules par points, nettoie les %)
  for col in df.columns:
    if col != 'player_name':
      df[col] = (
          df[col]
          .astype(str)
          .str.replace(',', '.')
          .str.replace('%', '')
          .str.strip()
      )
      df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0)

  return df


def read_local_csv(file_path, source_name):
  """Lit un fichier CSV local et le standardise directement."""
  if not os.path.exists(file_path):
    print(f'⚠️ Fichier introuvable : {file_path}')
    return None

  try:
    df_raw = pd.read_csv(file_path, sep=None, engine='python')
    return standardize_dataframe(df_raw, source_name)
  except Exception as e:
    print(f'Erreur lors de la lecture du fichier CSV : {e}')
    return None