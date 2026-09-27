# STBL – Projections fantasy NBA

Pipeline qui importe des projections de joueurs NBA depuis plusieurs sources,
les harmonise dans une base SQLite et relie les joueurs entre sources.
Objectif final : alimenter un Google Sheets d'optimisation de ligue fantasy.

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows (source .venv/bin/activate sous Linux/macOS)
pip install -r requirements.txt
```

## Utilisation

Toujours lancer depuis la racine du projet.

```bash
python main.py                          # réglages de config/settings.json
python main.py --stage ros              # projections "rest of season"
python main.py --sources fantasypros    # une seule source
python main.py --skip-link -v           # sans rapprochement, logs détaillés
python main.py --skip-import            # recalcule seulement les projections finales
python -m scripts.weighting --phase draft   # pondération d'une phase
python -m scripts.exports               # réexporte seulement les projections brutes

python main.py --file fanscout=C:\chemin\table.csv   # fichier CSV précis
python -m scripts.sources.cbs           # une source seule
python -m scripts.player_linker         # relancer le rapprochement
python -m scripts.tools.build_aliases alias.csv   # CSV -> config/player_aliases.json
```

## Configuration

| Fichier | Versionné | Rôle |
|---|---|---|
| `config/settings.json` | oui | Réglages d'exécution : saison active, étape (`draft`/`ros`), sources activées, phases de pondération, format d'export, réglages HTTP. |
| `config/sources.json` | oui | Description de chaque source : code court (grilles de pondération), URL ou dossier/fichiers d'import, échelle des pourcentages, correspondance colonnes source -> colonnes standard. |
| `config/league.json` | oui | Paramètres de la ligue pour les z-scores : `format` (`h2h` / `roto`), `teams`, `roster` (joueurs par poste, IL et BN compris), `categories` (poids de chaque catégorie, 0 = ignorée), `zscore.min_gp`, `zscore.iterations`. |
| `config/weights/<phase>.csv` | oui | Grilles de pondération des sources (GP / MIN / STATS), au format de la grille Google Sheets. Voir `config/weights/README.md`. |
| `config/player_aliases.json` | oui | `{"nom dans une source": "nom canonique"}` pour les joueurs que la normalisation ne suffit pas à relier (ex. `"Nic Claxton": "Nicolas Claxton"`). |

## Sources

| Source | Type | Récupération |
|---|---|---|
| CBS | web | automatique, une page par poste |
| FantasyPros | web | automatique |
| FanScout | fichier CSV | export manuel depuis [fanscout.pro/projections](https://fanscout.pro/projections), déposé dans `data/imports/fanscout/` sous le nom `fanscout_<étape>_AAAA-MM-JJ.csv` |
| LineupExperts | export navigateur | protégé par Cloudflare : favori `tools/lineupexperts_export.js` (voir `tools/README.md`) qui télécharge `lineupexperts_<étape>_AAAA-MM-JJ.csv`, à déposer dans `data/imports/lineupexperts/` |
| DraftKick | fichier CSV | export manuel, déposé dans `data/imports/draftkick/` sous le nom `draftkick_<étape>_AAAA-MM-JJ.csv` |

Pour les sources CSV, l'import prend le fichier le plus récent de l'étape active
(ou celui passé avec `--file`). Totaux ou moyennes par match : détection
automatique. Les CSV déposés ne sont pas versionnés.

## Structure

```
main.py                      orchestration du pipeline
scripts/config.py            chemins et chargement de la configuration
scripts/db.py                schéma SQLite, migration, écriture des projections
scripts/names.py             nettoyage des noms et clé de rapprochement
scripts/http_client.py       requêtes (timeout, nouvelles tentatives, pause)
scripts/sources/base.py      logique commune à toutes les sources
scripts/sources/csv_source.py  base des sources alimentées par un fichier CSV
scripts/sources/cbs.py       CBS Sports (une page par poste)
scripts/sources/fantasypros.py
scripts/sources/fanscout.py  FanScout (CSV)
scripts/sources/draftkick.py DraftKick (CSV)
scripts/sources/lineupexperts.py LineupExperts (CSV exporté depuis le navigateur)
tools/                       outils navigateur (export LineupExperts)
data/imports/<source>/       dépôt des exports CSV
scripts/player_linker.py     table players et rapprochement entre sources
scripts/exports.py           exports CSV (projections brutes, format commun)
scripts/weighting/weights.py lecture et validation des grilles de pondération
scripts/weighting/engine.py  calcul des projections finales et export CSV
scripts/weighting/zscores.py z-scores des 9 catégories (AVG et TOT), sommes et rangs
scripts/tools/build_aliases.py
```

Ajouter une source :
- **web** : une classe héritant de `ProjectionSource` (méthodes `page_requests` et `parse_page`) ;
- **CSV** : une classe de deux lignes héritant de `CsvProjectionSource` (voir `fanscout.py`) ;
  tout le reste se règle dans `sources.json` :
  - `import_dir`, `files` (motif du fichier par étape) ;
  - `player_column`, `team_column`, `positions_column` (un en-tête en double devient `Nom_2`) ;
  - `stat_mode` (`auto`, `totals`, `per_game`), `percent_scale` (1 ou 100) ;
  - `empty_as_zero` (case vide = 0), `missing_values` (ex. ADP 999 = vide), `skip_players` ;
  - `columns` (en-tête du CSV -> colonne standard) ;

puis un bloc dans `sources.json` et une ligne dans `scripts/sources/__init__.py`.

## Données

- `raw_projections` : une ligne par joueur, source, saison et étape (contrainte d'unicité).
  Colonnes : `player`, `player_key`, `team` (codes NBA officiels), `positions`
  (ex. `PG,SG`), puis les stats standard `gp gs min mpg pts reb ast stl blk tov
  fgm fga fgp ftm fta ftp fg3m fg3a fg3p fpts`, et pour le draft `adp`, `auction_cost`
  (coût moyen Yahoo, FanScout) et `auction_value` (valeur estimée, DraftKick).
- Stats dérivées automatiquement quand une source ne les donne pas :
  `mpg = min / gp`, `fgm = fgp × fga`, `ftm = ftp × fta`, et l'inverse.
- Toutes les stats sont des **totaux sur la saison** ; les pourcentages sont en **décimal (0.485)**.
- `players` : référentiel, un joueur par `name_key`.
- Vue `v_projections` : projections + nom canonique, prête pour l'export.
  Exportée à chaque lancement dans `exports/raw_<étape>_<saison>.csv` (toutes les sources,
  une ligne par joueur et par source, triée par joueur puis source).
- `final_projections` : projections finales pondérées, une ligne par phase, saison et joueur,
  avec `n_sources`, `sources`, `gp_coverage`, `min_coverage`, `stats_coverage` et
  `fga_estimated`, et les z-scores : `z_<cat>_avg` / `z_<cat>_tot` pour FG%, 3PM, FT%, REB,
  AST, STL, BLK, TO, PTS, leur somme pondérée `z_sum_avg` / `z_sum_tot` et le rang
  `rank_avg` / `rank_tot`. Exportées (triées par `rank_avg` en H2H, `rank_tot` en Roto) dans `exports/final_<phase>_<saison>.csv` (`;` et virgule
  décimale par défaut, réglable dans `settings.json` > `export`).

## Z-scores

- Groupe de référence = `teams` × taille du roster (15 × 14 = 210 joueurs) : moyenne et
  écart-type sont calculés sur les joueurs réellement draftés, en 3 passes (tous les
  joueurs, puis les 210 meilleurs, etc.).
- AVG = moyennes par match (utile en H2H), TOT = totaux sur la saison (Roto, intègre les matchs joués).
- FG% / FT% : z-score de l'impact `(pourcentage - pourcentage du groupe) × tentatives`.
- TO : signe inversé. Somme = Σ poids × z ; rang 1 = meilleure somme.
- Pas encore de rareté par poste (la répartition G/F/C sert seulement à la taille du groupe).

## Limites connues

- CBS n'affiche que les 100 premiers joueurs par poste (500 au total) et un seul poste par joueur.
- FantasyPros ne publie les projections `ros` qu'une fois la saison commencée ; en attendant l'import est annulé sans toucher aux données existantes.
- Un import dont une page échoue est entièrement annulé, pour ne jamais remplacer des données complètes par des données partielles.
