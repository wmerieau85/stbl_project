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
| `config/league.json` | oui | Paramètres de la ligue, à adapter chaque saison : `format` (`h2h` / `roto`), `platform`, `teams`, `roster` (joueurs par poste, IL et BN compris), `categories` (poids, 0 = ignorée), `zscore`, `games` (matchs par poste titulaire, alignements quotidiens), `draft` (snake, tours, ordre, mon équipe, keepers, réglages des simulations) et `google_sheets` (classeurs et onglets). |
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

## Assistant de draft (Rotisserie)

Le classeur Google Sheets reste l'interface : on saisit les choix dans `draft_res`
(colonne D), le script lit l'état de la draft et écrit ses recommandations dans l'onglet `reco`.

```bash
python main.py --push-sheet                # pipeline complet puis projections -> draft_bdd (A:BH)
python -m scripts.draft push-projections   # seulement l'envoi des projections vers draft_bdd
python -m scripts.draft config-push        # league.json -> onglet "config" (à faire une fois)
python -m scripts.draft config-pull        # onglet "config" -> league.json
python -m scripts.draft watch              # veille pendant la draft : recalcul à chaque choix saisi
python -m scripts.draft reco               # un seul calcul
python -m scripts.draft reco --xlsx draft2627.xlsx --until 40 --no-sheet   # test hors ligne / mock draft
```

Mise en place (une fois) :
1. Copier le JSON du compte de service dans `credentials/service_account.json` (ignoré par git),
   ou modifier `google.service_account_file` dans `config/settings.json`.
2. Partager le classeur de draft avec l'adresse `client_email` du compte de service, en Éditeur.
3. `google_sheets.draft_spreadsheet_id` dans `config/league.json` : l'identifiant dans l'URL
   `docs.google.com/spreadsheets/d/<ID>/edit` (seul réglage à laisser dans le JSON).
4. `python -m scripts.draft config-push` crée l'onglet `config` : paramètres de la ligue
   (section / paramètre / valeur / aide) puis le tableau `Ordre | Manager | Keeper 1 | Keeper 2`
   (ordre du 1er tour). Ensuite on modifie l'onglet, plus le JSON : `reco` et `watch` relisent
   l'onglet à chaque lancement et mettent `league.json` à jour (`--no-sync-config` pour l'éviter).

Projections : `push-projections` écrit directement dans `draft_bdd`, colonnes A à BH (même
disposition que l'ancien onglet `export`), sans toucher aux formules des colonnes BJ et
suivantes. La formule IMPORTRANGE de A1 est remplacée par les valeurs. Onglet et colonne de
départ réglables (`projections_tab`, `projections_start_col`, `projections_spreadsheet_id`
si l'onglet est dans un autre classeur).

Keepers : `draft.keeper_rounds` = tours occupés par les keepers (`[1, 2]` : le 1er keeper
prend le choix du manager au tour 1, le 2e au tour 2) ; vide si les keepers s'ajoutent aux
tours. Un keeper saisi dans `draft_res` par son propre manager n'est jamais compté deux fois.

Calcul :
- tour et manager déduits de l'ordre snake de `draft.order` (tour impair : ordre normal,
  tour pair : ordre inversé) ;
- effectif de chaque équipe = keepers + choix saisis ; les totaux saison respectent le plafond
  `games.per_slot` × postes titulaires (82 × 8 = 656) : les meilleurs joueurs par match
  (z AVG) jouent en priorité ;
- pour chaque candidat, `draft.simulations` tirages de la suite de la draft : les autres
  managers suivent l'ADP (bruit `draft.adp_noise`, joueurs sans ADP placés d'après leur rang
  TOT), nos choix suivants prennent le meilleur z TOT compatible avec les postes G / F / C ;
- chaque tirage donne un classement roto projeté (points espérés par catégorie, FG% et FT%
  recalculés sur les tirs de l'équipe) ; la recommandation classe les candidats selon les
  points roto espérés de notre équipe, avec la probabilité qu'ils soient encore disponibles
  à notre choix et au choix suivant (pour savoir si l'on peut attendre).

L'onglet `reco` contient : l'état de la draft, les 20 meilleurs candidats (points espérés, écart
avec le n°1, disponibilité, points par catégorie), le classement roto projeté, le classement
des effectifs actuels avec leurs totaux, et mon équipe. Même contenu dans `exports/reco_<saison>.csv`.
Les noms saisis sont reconnus sans tenir compte des accents, de la casse ni des suffixes ; sinon
ajouter un alias dans `config/player_aliases.json`.

## Yahoo Fantasy (API)

Lecture seule, via l'application déclarée sur developer.yahoo.com (redirect URI `https://localhost:8080`).

1. Créer `credentials/yahoo_app.json` (dossier ignoré par git) :
   `{"client_id": "<Client ID>", "client_secret": "<Client Secret>"}`
2. `python -m scripts.yahoo auth` : ouvre la page Yahoo ; après « Autoriser », le navigateur
   arrive sur `https://localhost:8080/?code=...` (page qui ne s'affiche pas, c'est normal) :
   coller l'adresse complète dans le terminal. Le jeton est enregistré dans
   `credentials/yahoo_token.json` et renouvelé automatiquement.
3. `python -m scripts.yahoo leagues` : liste mes ligues NBA avec leur ID, à reporter dans
   l'onglet config (« ID de la ligue Yahoo »).
4. `python -m scripts.yahoo check` : réglages, équipes et managers, nombre de choix de draft.
   `python -m scripts.yahoo draft` : choix effectués (+ `exports/yahoo_draft_<saison>.csv`).

Tant que Yahoo n'a pas validé l'accès à l'API (erreur 403), la ligue étant publique, les choix
sont lus sur la page `basketball.fantasysports.yahoo.com/nba/<ID>/draftresults` : aucun réglage
à faire, le programme bascule tout seul. La colonne « Équipe Yahoo » de l'onglet config relie
chaque équipe Yahoo à un manager ; l'ordre réel de Yahoo (choix par choix) remplace alors l'ordre
snake de la config, avec une alerte en cas d'écart et si un choix des tours keepers ne correspond
pas aux keepers déclarés.

Pendant la draft (`Source des choix` = `yahoo` dans l'onglet config), `watch` lit les choix
directement dans Yahoo, les recopie dans la colonne D de `draft_res` (réglable) et signale tout
écart entre l'ordre Yahoo et l'ordre de la config. Si Yahoo ne répond pas, lecture de
`draft_res` à la place : la saisie manuelle reste possible.

## Module saison

```
python main.py --stage ros                 # projections ROS (lt) + stats par période (st)
python -m scripts.season update            # onglets season et yahoo_rosters
python -m scripts.season update --no-sheet # console + exports/season_<saison>.csv
python -m scripts.season update --rosters sheet  # effectifs de l'onglet rosters (simulation de draft)
```

Projection de fin de saison de chaque équipe = stats réelles (classement Yahoo) + 15 prochains
jours en phase st + reste de la saison en phase lt, avec le calendrier NBA (NBA.com, sinon
fixturedownload.com) et les plafonds de matchs restants par poste (G, F, C, Util) lus sur la page
de chaque équipe. Onglet season : classement projeté (points espérés, chances de titre et de
podium), mes catégories (ce qu'il faut pour gagner un point, marge avant d'en perdre un), mes
matchs par poste, stats projetées de toutes les équipes. Sans projections lt, la phase draft
est utilisée. Effectifs : Yahoo par défaut, ou un onglet du classeur (« Source des effectifs » =
`sheet` dans l'onglet config, colonnes Player et Team repérées par leur en-tête) ; les plafonds de
matchs sont alors ceux de la saison complète (82 x postes).

Limites : les tentatives de tirs réelles (FGA, FTA) ne sont pas publiées par Yahoo et sont
estimées ; l'alignement jour par jour (plus de joueurs que de postes certains soirs) sera traité
par le module rotation.

## Limites connues

- CBS n'affiche que les 100 premiers joueurs par poste (500 au total) et un seul poste par joueur.
- FantasyPros ne publie les projections `ros` qu'une fois la saison commencée ; en attendant l'import est annulé sans toucher aux données existantes.
- Un import dont une page échoue est entièrement annulé, pour ne jamais remplacer des données complètes par des données partielles.
