# STBL – Projections fantasy NBA

Pipeline qui importe des projections de joueurs NBA depuis plusieurs sources,
les harmonise dans une base SQLite et relie les joueurs entre sources.
Objectif final : alimenter un Google Sheets d'optimisation de ligue fantasy.

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows (source .venv/bin/activate sous Linux/macOS)
pip install -r requirements.txt
copy config\settings.example.json config\settings.json   # puis adapter
```

## Utilisation

Toujours lancer depuis la racine du projet.

```bash
python main.py                          # réglages de config/settings.json
python main.py --stage ros              # projections "rest of season"
python main.py --sources fantasypros    # une seule source
python main.py --skip-link -v           # sans rapprochement, logs détaillés

python -m scripts.sources.cbs           # une source seule
python -m scripts.player_linker         # relancer le rapprochement
python -m scripts.tools.build_aliases alias.csv   # CSV -> config/player_aliases.json
```

## Configuration

| Fichier | Versionné | Rôle |
|---|---|---|
| `config/settings.json` | non | Saison active, étape (`draft`/`ros`), sources activées, réglages HTTP. Modèle : `settings.example.json`. |
| `config/mappings.json` | oui | URL de chaque source, échelle des pourcentages, correspondance colonnes source -> colonnes standard. |
| `config/player_aliases.json` | oui | `{"nom dans une source": "nom canonique"}` pour les joueurs que la normalisation ne suffit pas à relier (ex. `"Nic Claxton": "Nicolas Claxton"`). |

## Structure

```
main.py                      orchestration du pipeline
scripts/config.py            chemins et chargement de la configuration
scripts/db.py                schéma SQLite, migration, écriture des projections
scripts/names.py             nettoyage des noms et clé de rapprochement
scripts/http_client.py       requêtes (timeout, nouvelles tentatives, pause)
scripts/sources/base.py      logique commune à toutes les sources
scripts/sources/cbs.py       CBS Sports (une page par poste)
scripts/sources/fantasypros.py
scripts/player_linker.py     table players et rapprochement entre sources
scripts/tools/build_aliases.py
```

Ajouter une source : une classe héritant de `ProjectionSource` (méthodes
`page_requests` et `parse_page`), un bloc dans `mappings.json`, et une ligne
dans `scripts/sources/__init__.py`.

## Données

- `raw_projections` : une ligne par joueur, source, saison et étape (contrainte d'unicité).
  Colonnes : `player`, `player_key`, `team` (codes NBA officiels), `positions`
  (ex. `PG,SG`), puis les stats standard `gp gs min mpg pts reb ast stl blk tov
  fgm fga fgp ftm fta ftp fg3m fg3a fg3p fpts`.
- Toutes les stats sont des **totaux sur la saison** ; les pourcentages sont en **décimal (0.485)**.
- `players` : référentiel, un joueur par `name_key`.
- Vue `v_projections` : projections + nom canonique, prête pour l'export.

## Limites connues

- CBS n'affiche que les 100 premiers joueurs par poste (500 au total) et un seul poste par joueur.
- FantasyPros ne publie les projections `ros` qu'une fois la saison commencée ; en attendant l'import est annulé sans toucher aux données existantes.
- Un import dont une page échoue est entièrement annulé, pour ne jamais remplacer des données complètes par des données partielles.
