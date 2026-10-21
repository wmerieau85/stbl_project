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
python main.py                          # réglages de l'onglet settings
python main.py --stage ros              # projections "rest of season"
python main.py --sources fantasypros    # une seule source
python main.py --skip-link -v           # sans rapprochement, logs détaillés
python main.py --skip-import            # recalcule seulement les projections finales
python -m scripts.weighting --phase draft   # pondération d'une phase
python -m scripts.exports               # réexporte seulement les projections brutes

python main.py --file fanscout=C:\chemin\table.csv   # fichier CSV précis
python -m scripts.sources.cbs           # une source seule
python -m scripts.player_linker         # relancer le rapprochement
```

## Récapitulatif des commandes

Toutes se lancent depuis la racine du projet, venv activé. L'onglet `settings` est relu à chaque
lancement (saison, étape `Actual Phase`, sources, grilles).

| Quand | Commande | À quoi elle sert |
|---|---|---|
| Une fois | `python -m scripts.yahoo auth` | autorise l'accès à l'API Yahoo (jeton dans `credentials/`) |
| Une fois par saison | `python -m scripts.yahoo leagues` | affiche l'ID de mes ligues, à reporter dans `Settings \| League \| League ID` |
| Contrôle | `python -m scripts.yahoo check` | réglages, équipes, managers et nombre de choix de la ligue |
| Contrôle | `python -m scripts.config_sheet check` | vérifie l'onglet settings sans rien écrire |
| Préparation | `python main.py --push-sheet` | **commande principale** : importe les sources actives de l'étape, rapproche les joueurs, récupère le pré-classement et l'ADP Yahoo (étape draft), calcule les phases de l'étape (draft ; ou lt / st en ros), puis écrit `bdd` et `bdd_detail` |
| Préparation | `python main.py --skip-import --push-sheet` | recalcule seulement (après un changement de grille ou d'alias) |
| Préparation | `python main.py --sources ninecat --push-sheet` | réimporte une seule source |
| Préparation | `python -m scripts.yahoo rankings` | seulement le pré-classement / ADP Yahoo (+ `exports/yahoo_rankings_<saison>.csv`) |
| Préparation | `python -m scripts.draft push-projections` | seulement l'envoi de la base vers `bdd` et `bdd_detail` |
| Avant la draft | `python -m scripts.lottery [--draw]` | tirage au sort de l'ordre de draft (onglet lottery) |
| Avant la draft | `python -m scripts.draft reco --xlsx <fichier> --until N --no-sheet` | test sur une mock draft, sans toucher au classeur |
| Pendant la draft | `python -m scripts.draft watch` | relit les choix (Yahoo ou bdd) et met à jour `draft_reco` à chaque nouveau choix |
| Pendant la draft | `python -m scripts.draft reco` | un seul recalcul |
| Après la draft | `python -m scripts.yahoo draft` | choix effectués (+ `exports/yahoo_draft_<saison>.csv`, bloc draft de l'onglet `yahoo`) |
| En saison | `python -m scripts.yahoo rosters` | effectifs actuels des 15 équipes (+ `exports/yahoo_rosters_<saison>.csv`, bloc rosters de l'onglet `yahoo`) |
| En saison | `python main.py --push-sheet` avec `Actual Phase = ros` | projections ROS + stats par période, phases lt / st |
| En saison | `python -m scripts.season update` | classement projeté et effectifs, onglet `season_reco` |
| En saison | `python -m scripts.season moves` | échanges ou joueurs libres simulés (lignes `Moves | ...`), onglet `moves_reco` |
| Maintenance | `python -m scripts.player_linker` | relance le rapprochement des joueurs |
| Maintenance | `python -m scripts.config_sheet push` | réécrit les onglets settings / players depuis la configuration locale (remise à plat) |
| Maintenance | `python -m pytest -q` | tests |

Étape active (`Actual Phase`) : `draft` n'importe que les sources actives en draft et ne recalcule
que la phase draft ; `ros` importe les projections ros et les stats par période, et ne recalcule
que lt / st. Les autres phases restent telles quelles en base et sont réécrites à l'identique dans
`bdd`. Une phase sans aucune donnée garde son calcul précédent.

## Configuration

L'onglet `settings` du classeur Google Sheets fait foi : on ne modifie plus de JSON à la main.
Une ligne par information, quatre colonnes `Section | Paramètre | Valeur | Aide`, ordre libre :

| Section | Contenu |
|---|---|
| `Settings \| League / Roster Positions / Scoring / Phases / Draft` | saison et étape actives, format, ligue Yahoo, mon équipe, postes, poids des catégories, grilles lt / st, snake, tours, tours des keepers |
| `Sheets \| Settings / Tab / Draft / Projections / Season` | ID du classeur, noms des onglets, source des choix de draft et des effectifs (`yahoo` / `sheet`) |
| `Optim \| Draft / Season` | z-scores, veille, candidats, simulations, horizon court terme |
| `Pipeline \| Import / Export` | réglages HTTP, séparateur et décimale des CSV |
| `Draft \| Order`, `Draft \| Keepers`, `Transco \| Managers` | ordre du 1er tour, keepers (mode `sheet` seulement : sinon Yahoo fait foi), équipes Yahoo |
| `Sources \| Types / Draft / RoS / Active / Import Files / Sea / L30 / L15 / L07` | par code de site (`cbs`, `fp`, `fs`, `dk`, `le`, `nc`, `fn`, `es`, `rb`) : URL ou fichier par étape, sources actives par étape, dossiers d'import, fenêtres de stats réelles |
| `Grid <phase> \| GP / MIN / STATS / <catégorie>` | grilles de pondération : code de la ligne « site » -> poids (chaque niveau = 100 %) |
| `Transco \| <colonne> / percent_scale / Player / Team / Positions...` | lecture des sites : colonne du site pour chaque colonne standard, options de lecture |

Les alias de joueurs sont dans l'onglet `players`. `main.py`, `reco`, `watch`,
`season update` et `yahoo` relisent les onglets à chaque lancement (`--no-sync-config` pour
l'éviter) : une erreur de saisie arrête le calcul avec un message, une ligne inconnue est signalée.

| Fichier | Versionné | Rôle |
|---|---|---|
| `config/bootstrap.json` | oui | ce qu'il faut avant de lire le classeur : `spreadsheet_id`, `config_tab`, chemins des identifiants Google / Yahoo (jamais les secrets). Les lignes `Sheets | Settings | ID` et `Sheets | Tab | Config` du classeur sont indicatives (alerte si elles diffèrent). |
| `config/config.json` | non | copie locale des onglets, réécrite à chaque relecture (permet de travailler hors ligne). |
| `config/defaults.json` | oui | configuration livrée, utilisée tant que `config.json` n'existe pas (et par les tests). |

```bash
python -m scripts.config_sheet pull      # onglets settings + players -> config/config.json
python -m scripts.config_sheet check     # contrôle l'onglet sans rien écrire
python -m scripts.config_sheet push      # remise à plat des onglets depuis la configuration locale
python -m scripts.config_sheet migrate   # ancien format (blocs) -> nouveau, copie dans config_old
```

Codes de la ligne « site » des grilles, sans année = saison active (`Settings | League | Actual Season`) :
`fs` (FanScout, pré-saison dans la grille draft, rest of season dans les grilles lt / st), `fp.ros`
(FantasyPros, rest of season), `fp.sea` / `fp.l30` / `fp.l15` / `fp.l07` (stats réelles), `draft` / `lt`
(projections finales d'une phase calculée avant). Avec une année (`fs26`, `fp26.ros`), le code vise une
saison précise : à réserver au mélange avec une autre saison (avertissement si elle diffère de la saison
active). Changer de saison = changer `Actual Season` : la base garde chaque saison à part (colonne
season) et tous les onglets n'affichent que la saison active.

## Sources

| Source | Type | Récupération |
|---|---|---|
| CBS | web | automatique, une page par poste |
| FantasyPros | web | automatique |
| ESPN (code `es`) | API publique | automatique, projections de saison complètes |
| RotoBaller (code `rb`) | API publique | automatique, projections 9-cat par match ; stats pondérées à 10 %, GP à 0 % |
| FanScout | fichier CSV | export manuel depuis [fanscout.pro/projections](https://fanscout.pro/projections), déposé dans `data/imports/fanscout/` sous le nom `fanscout_<étape>_AAAA-MM-JJ.csv` |
| LineupExperts | export navigateur | protégé par Cloudflare : favori `tools/lineupexperts_export.js` (voir `tools/README.md`) qui télécharge `lineupexperts_<étape>_AAAA-MM-JJ.csv`, à déposer dans `data/imports/lineupexperts/` |
| DraftKick | fichier CSV | export manuel, déposé dans `data/imports/draftkick/` sous le nom `draftkick_<étape>_AAAA-MM-JJ.csv` |
| 9cat (9 Fantasy, code `nc`) | API JSON | automatique : projections « Expected » de [9cat.co.il](https://9cat.co.il/en/basketball/players/nba-103) (API publique 365scores, moyennes par match + matchs projetés, FGM/FGA et FTM/FTA fournis) ; `nc.sea` = stats réelles de la saison, une fois celle-ci commencée |
| Fantasy Nerds (code `fn`) | API payante | clé personnelle dans `credentials/fantasynerds_key.txt` (une ligne, ignoré par git) ou variable `FANTASYNERDS_API_KEY` ; projections de draft en totaux, FG% / FT% sans tentatives (estimées). Désactivée par défaut (`Sources | Active`) |

ESPN et RotoBaller sont activés pour la draft, pas pour le RoS. RotoBaller publie des moyennes
par match sans nombre de matchs projeté : elles sont donc converties en totaux avec les GP
pondérés des autres sources. Son poids GP reste à 0 % ; ses stats ont 10 % de poids chacune.
Les poids des cinq sources existantes sont réduits proportionnellement pour préserver leurs
poids relatifs. ESPN reçoit 16,67 % du poids GP et des minutes, et 10 % du poids des stats.

Pour les sources CSV, l'import prend le fichier le plus récent de l'étape active
(ou celui passé avec `--file`). Totaux ou moyennes par match : détection
automatique. Les CSV déposés ne sont pas versionnés.

Lorsqu'un onglet `settings` existant ne connaît pas encore les codes `es` et `rb`, sa lecture
(`python -m scripts.config_sheet pull`, ou la synchronisation habituelle du pipeline) ajoute automatiquement
les deux sources et les poids livrés à la copie locale, en conservant les poids relatifs des sources
déjà configurées. Cela ne modifie pas le classeur Google Sheets. Pour y faire apparaître les nouveaux
réglages, utilisez `python -m scripts.config_sheet push` après avoir vérifié la copie locale : cette
commande réécrit tout l'onglet `settings` et `players`.

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
scripts/sources/ninecat.py     9cat.co.il (API 365scores)
scripts/sources/fantasynerds.py Fantasy Nerds (API, clé personnelle)
tools/                       outils navigateur (export LineupExperts)
data/imports/<source>/       dépôt des exports CSV
scripts/player_linker.py     table players et rapprochement entre sources
scripts/exports.py           exports CSV (projections brutes, format commun)
scripts/weighting/weights.py lecture et validation des grilles de pondération
scripts/weighting/engine.py  calcul des projections finales et export CSV
scripts/weighting/zscores.py z-scores des 9 catégories (AVG et TOT), sommes et rangs
```

Ajouter une source :
- **web** : une classe héritant de `ProjectionSource` (méthodes `page_requests` et `parse_page`) ;
- **CSV** : une classe de deux lignes héritant de `CsvProjectionSource` (voir `fanscout.py`) ;
  tout le reste se règle dans l'onglet settings (sections Sources et Transco) :
  - `import_dir`, `files` (motif du fichier par étape) ;
  - `player_column`, `team_column`, `positions_column` (un en-tête en double devient `Nom_2`) ;
  - `stat_mode` (`auto`, `totals`, `per_game`), `percent_scale` (1 ou 100) ;
  - `empty_as_zero` (case vide = 0), `missing_values` (ex. ADP 999 = vide), `skip_players` ;
  - `columns` (en-tête du CSV -> colonne standard) ;

puis sa configuration dans `config/defaults.json` > `sources` (code, `urls` ou `files`) et une ligne dans `scripts/sources/__init__.py`.

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
  décimale par défaut, réglable dans l'onglet settings, `Pipeline | Export`).

## Z-scores

- Groupe de référence = `teams` × taille du roster (15 × 14 = 210 joueurs) : moyenne et
  écart-type sont calculés sur les joueurs réellement draftés, en 3 passes (tous les
  joueurs, puis les 210 meilleurs, etc.).
- AVG = moyennes par match (utile en H2H), TOT = totaux sur la saison (Roto, intègre les matchs joués).
- FG% / FT% : z-score de l'impact `(pourcentage - pourcentage du groupe) × tentatives`.
- TO : signe inversé. Somme = Σ poids × z ; rang 1 = meilleure somme.
- Pas encore de rareté par poste (la répartition G/F/C sert seulement à la taille du groupe).

## Assistant de draft (Rotisserie)

Le classeur Google Sheets reste l'interface : on saisit le manager de chaque joueur drafté dans la
colonne `Team Draft` (A) de l'onglet `bdd`, sur sa ligne de phase `draft` ; le script en déduit
l'état de la draft (ordre snake, keepers dans leurs tours) et écrit ses recommandations dans
`draft_reco`, que l'onglet `board` (draft board) reprend par formules.

```bash
python main.py --push-sheet                # pipeline complet puis projections (toutes phases) -> bdd
python -m scripts.draft push-projections   # seulement l'envoi des projections vers bdd
python -m scripts.draft config-push        # configuration locale -> onglets "settings" et "players"
python -m scripts.draft config-pull        # onglets -> config/config.json
python -m scripts.draft watch              # veille pendant la draft : recalcul à chaque choix saisi
python -m scripts.draft reco               # un seul calcul
python -m scripts.draft reco --xlsx draft2627.xlsx --until 40 --no-sheet   # test hors ligne / mock draft
```

Mise en place (une fois) :
1. Copier le JSON du compte de service dans `credentials/service_account.json` (ignoré par git),
   ou modifier `google.service_account_file` dans `config/bootstrap.json`.
2. Partager le classeur de draft avec l'adresse `client_email` du compte de service, en Éditeur.
3. `spreadsheet_id` dans `config/bootstrap.json` : l'identifiant dans l'URL
   `docs.google.com/spreadsheets/d/<ID>/edit`.
4. `python -m scripts.config_sheet push` crée les onglets `settings` et `players` (voir
   Configuration) ; ensuite on ne modifie plus que les onglets.

Projections : `push-projections` écrit toutes les phases de la saison (draft, lt, st, season)
dans l'onglet `bdd`, colonnes C à BK : C = phase, puis la disposition de l'ancien onglet `export`
(joueur en D). Les colonnes A `Team Draft` et B `Team Season` sont des propriétés du joueur :
elles sont conservées et reportées sur toutes ses lignes, même si l'ordre change. Les colonnes à
droite (formules) ne sont pas touchées. Pour une seule phase : `--phase lt`. Onglet et colonne de
départ réglables dans l'onglet settings (section Projections).

Détail par source : `push-projections` (et `python main.py --push-sheet`) écrit aussi l'onglet
`bdd_detail` (réglable : `Sheets | Tab | Detail`, vide = pas d'onglet ; seul :
`python -m scripts.draft push-detail`). Une ligne par joueur et par source (colonne Stage :
draft, ros, sea, l30...) et une ligne `Pondéré` par phase, triées par rang final : stats par match,
ADP, EFF et RANG AVG / TOT calculés avec le groupe de référence de la phase pondérée (une source
optimiste sur un joueur lui donne un meilleur rang). Sans tirs tentés, les tentatives par match de
la projection pondérée sont reprises avec le % de la source (colonne `Tirs estimés`).

Phase `season` (étape ros) : stats réelles de la saison en cours (`Grid season`, par défaut
`fp.sea` à 100 %), avec z-scores et rangs comme les autres phases.

Tirage au sort de l'ordre de draft (bloc lottery de l'onglet `board`) :

```bash
python -m scripts.lottery              # boules des managers encore dans le tirage -> onglet lottery
python -m scripts.lottery --draw       # ... puis tire une boule au hasard
python -m scripts.lottery --dry-run    # affiche sans écrire
```

Plages dans l'onglet settings (`Draft Lottery | Managers / Active / Number Balls / Lottery
Number`) : managers dont `Active` = 0, nombre de boules de la colonne du tirage en cours (1 ou 2).
Boules numérotées de 1 à N, mélangées, écrites en `Ball | Manager` (`Draft Lottery | Balls`,
par défaut `lottery!A1`).

Keepers : `Settings | Draft | Keeper rounds` = tours occupés par les keepers (`1, 2` : le 1er keeper
prend le choix du manager au tour 1, le 2e au tour 2) ; vide si les keepers s'ajoutent aux
tours. Un keeper inscrit dans `Team Draft` pour son propre manager n'est jamais compté deux fois.

Calcul :
- tour et manager déduits de l'ordre snake de `draft.order` (tour impair : ordre normal,
  tour pair : ordre inversé) ;
- effectif de chaque équipe = keepers + choix saisis ; les totaux saison respectent le plafond
  `games.per_slot` × postes titulaires (82 × 8 = 656) : les meilleurs joueurs par match
  (z AVG) jouent en priorité ;
- tirages de la suite de la draft, tous partagés par les candidats (même ordre des autres
  managers pour tous, comparaison à hasard égal) : `Simulations min` (10) tirages pour tous, puis
  tous les 10 tirages on écarte ceux dont l'écart au meilleur dépasse `Prune z` (2) écarts-types,
  sans descendre sous `Finalists` (5) ; les finalistes vont jusqu'à `Simulations` (200) tirages
  ou `Time budget` (8 s). La disponibilité est mesurée sur tous les tirages, pour tous les
  candidats. Colonnes `± pts (95 %)` et `Tirages` dans draft_reco. Dans la suite simulée, les autres
  managers suivent l'ADP (bruit `draft.adp_noise`, joueurs sans ADP placés d'après leur rang
  TOT), nos choix suivants prennent le meilleur z TOT compatible avec les postes G / F / C ;
- mode `need` (`Optim | Draft | Opponent model`, par défaut) : chaque manager départage les
  `Need candidates` (4) prochains joueurs de son ADP selon les besoins de son équipe (pente de
  ses points roto par catégorie, équipes comparées en moyenne par joueur et en %). Le poids des
  besoins vaut 0 au 1er tour et monte jusqu'à `Need weight` (2) au dernier : l'ADP reste la base.
  `adp` = ancien comportement ; `--model adp|need` pour comparer sans toucher l'onglet settings ;
- chaque tirage donne un classement roto projeté (points espérés par catégorie, FG% et FT%
  recalculés sur les tirs de l'équipe) ; la recommandation classe les candidats selon les
  points roto espérés de notre équipe, avec la probabilité qu'ils soient encore disponibles
  à notre choix et au choix suivant (pour savoir si l'on peut attendre).

L'onglet `reco` contient : l'état de la draft, les 20 meilleurs candidats (points espérés, écart
avec le n°1, disponibilité, points par catégorie), le classement roto projeté, le classement
des effectifs actuels avec leurs totaux, et mon équipe. Même contenu dans `exports/reco_<saison>.csv`.
Les noms saisis sont reconnus sans tenir compte des accents, de la casse ni des suffixes ; sinon
ajouter un alias dans l'onglet `players`.

## Yahoo Fantasy (API)

Lecture seule, via l'application déclarée sur developer.yahoo.com (redirect URI `https://localhost:8080`).

1. Créer `credentials/yahoo_app.json` (dossier ignoré par git) :
   `{"client_id": "<Client ID>", "client_secret": "<Client Secret>"}`
2. `python -m scripts.yahoo auth` : ouvre la page Yahoo ; après « Autoriser », le navigateur
   arrive sur `https://localhost:8080/?code=...` (page qui ne s'affiche pas, c'est normal) :
   coller l'adresse complète dans le terminal. Le jeton est enregistré dans
   `credentials/yahoo_token.json` et renouvelé automatiquement.
3. `python -m scripts.yahoo leagues` : liste mes ligues NBA avec leur ID, à reporter dans
   l'onglet settings (« ID de la ligue Yahoo »).
4. `python -m scripts.yahoo check` : réglages, équipes et managers, nombre de choix de draft.
   `python -m scripts.yahoo draft` : choix effectués (+ `exports/yahoo_draft_<saison>.csv` et bloc draft
   de l'onglet `yahoo`). `python -m scripts.yahoo rosters` : effectif actuel de chaque équipe (poste,
   statut, équipe NBA ; matchs restants par poste affichés dans la console), dans
   `exports/yahoo_rosters_<saison>.csv` et le bloc rosters de l'onglet `yahoo`. Dans les deux cas, la
   colonne `player` porte le nom de référence (base + alias de l'onglet players, comme dans `bdd`) et
   la dernière colonne, `yahoo_name`, le nom affiché par Yahoo ; un joueur inconnu de la base est
   signalé (alias à ajouter). Blocs fixes : draft en A:K, rosters en L:U, rankings à partir de V.
5. `python -m scripts.yahoo rankings` : pré-classement Yahoo (O-Rank, l'ordre de la salle de draft
   de ma ligue) et ADP Yahoo (`average_pick`) des `Yahoo rankings count` (300) premiers joueurs,
   enregistrés en base (table `yahoo_rankings`) et dans `exports/yahoo_rankings_<saison>.csv`.
   Fait aussi par `python main.py` à l'étape draft (`--skip-yahoo` pour s'en passer ; sans jeton
   ou sans accès, simple avertissement). Colonnes `Yahoo` (rang) et `ADP Yahoo` de `bdd` (BK, BL) ;
   sans classement récupéré, `Yahoo` reprend l'ordre ADP des sources. Joueurs absents des
   projections : listés dans le log (alias à ajouter dans l'onglet players si le nom diffère).
6. `python -m scripts.yahoo stats` : standings Yahoo (statistiques et points roto par catégorie)
   et Team Log (cumuls de saison de chaque joueur ayant rapporté des points à chaque équipe,
   y compris les joueurs qui ne sont plus dans son roster) dans l'onglet `yahoo`. Le Team Log
   n'est disponible qu'après la fin de la première semaine de matchs : d'ici là, les standings sont
   écrits quand même et le bloc Team Log reste vide (simple avertissement). Les deux blocs sont écrits
   à droite des résultats de draft, rosters et rankings déjà présents.

Tant que Yahoo n'a pas validé l'accès à l'API (erreur 403), la ligue étant publique, les choix
sont lus sur la page `basketball.fantasysports.yahoo.com/nba/<ID>/draftresults` (assistant de draft,
`watch` et `python -m scripts.yahoo draft`) : aucun réglage à faire, le programme bascule tout seul.
`rosters`, `rankings` et `stats` passent toujours par les pages publiques. La colonne « Équipe Yahoo » de l'onglet settings relie
chaque équipe Yahoo à un manager ; l'ordre réel de Yahoo (choix par choix) remplace alors l'ordre
snake de la config, avec une alerte en cas d'écart et si un choix des tours keepers ne correspond
pas aux keepers déclarés.

Pendant la draft (`Source des choix` = `yahoo` dans l'onglet settings), `watch` lit les choix
directement dans Yahoo, les recopie dans la colonne `Team Draft` de `bdd` (réglable) et signale tout
écart entre l'ordre Yahoo et l'ordre de la config. Si Yahoo ne répond pas, lecture de
`Team Draft` à la place : la saisie manuelle reste possible.

## Module saison

```
python main.py --stage ros                 # projections ROS (lt) + stats par période (st)
python -m scripts.season update            # onglet season_reco (+ exports/season_reco_<saison>.csv et season_reco_rosters_<saison>.csv)
python -m scripts.season update --no-sheet # console + exports/season_<saison>.csv
python -m scripts.season update --rosters sheet  # effectifs de bdd : Team Season (sinon Team Draft)
```

Projection de fin de saison de chaque équipe = stats réelles (classement Yahoo) + 15 prochains
jours en phase st + reste de la saison en phase lt, avec le calendrier NBA (NBA.com, sinon
fixturedownload.com) et les plafonds de matchs restants par poste (G, F, C, Util) lus sur la page
de chaque équipe. Onglet season : classement projeté (points espérés, chances de titre et de
podium), mes catégories (ce qu'il faut pour gagner un point, marge avant d'en perdre un), mes
matchs par poste, stats projetées de toutes les équipes et matchs ST/LT attendus et affectés par
joueur dans chacun des 15 rosters. Les 14 autres équipes servent de référence : lineup standard (valeur par match
d'abord, sous les plafonds), chaque match attendu n'étant aligné qu'avec le taux d'alignement du
manager (`scripts/season/opponents.py` : GP réels / GP possibles à date, mélangé au taux médian de la
ligue tant que la saison est jeune ; réglages `Optim | Season | Opponent model / Opponent default
rate / Opponent ramp (days)`). Seule l'affectation ST/LT de mon équipe maximise ensuite le gain
marginal de points roto attendu face à elles, sous les plafonds G / F / C / Util. L'onglet
s'appelle désormais `season_reco` (`Sheets | Tab | Season Reco` ; l'ancien libellé `Season` et
l'ancien onglet `season` sont repris et renommés) ; il contient aussi le bloc Adversaires et les
effectifs des 15 équipes. Les matchs écartés
par cette optimisation sont distingués des matchs perdus faute de plafond. Le détail des 15 rosters
est aussi dans l'export `exports/season_reco_rosters_<saison>.csv`.
Sans projections lt, la phase draft est utilisée. Effectifs : Yahoo par défaut, ou le classeur
(`Sheets | Season | Rosters Source` =
`sheet` : colonne `Team Season` de `bdd`, sinon `Team Draft`) ; les plafonds de
matchs sont alors ceux de la saison complète (82 x postes).

Limites : les tentatives de tirs réelles (FGA, FTA) ne sont pas publiées par Yahoo et sont
estimées ; l'alignement jour par jour (plus de joueurs que de postes certains soirs) sera traité
par le module rotation.

### Simulation de mouvements (moves_reco)

`python -m scripts.season moves` (option `--no-sheet` : console + `exports/moves_reco_<saison>.csv`).
Réglages dans l'onglet settings :

| Ligne | Colonne 2 | Colonne 3 |
|---|---|---|
| `Moves | Player 1` à `4` | manager : le mien (je cède ces joueurs) ou celui de `Moves | Compare` (je les reçois) | joueur (vide = ignoré) |
| `Moves | Compare` | `manager` | nom du manager |
| `Moves | Compare` | `free agent` | (vide) : je lâche les joueurs listés, qui doivent être les miens |
| `Optim | Moves` | `Value tolerance` | écart de valeur accepté, 10 % par défaut |
| `Optim | Moves` | `Max results` / `Min results` / `Free agent results` / `Free agent shortlist` | 20 / 10 / 6 / 15 |

- Échange : autant de joueurs reçus que cédés. Toutes les combinaisons de l'autre effectif sont
  testées ; à valeur équivalente (tolérance), les 20 meilleurs gains pour moi sont gardés, complétés
  jusqu'à 10 par les combinaisons les plus proches en valeur (« hors tolérance »). Affichage du plus
  équitable au moins équitable, avec le gain de l'autre manager (un échange qui le fait
  perdre a peu de chances d'être accepté), le rang projeté, les chances de titre / podium et l'effet
  par catégorie.
- Joueurs libres (joueurs des projections absents de tous les effectifs Yahoo, statut waivers non
  vérifié) : chacun est testé à la place des joueurs lâchés ; si plusieurs sont lâchés, les 15
  meilleurs sont combinés. Les 6 meilleures options sont affichées, même si elles font perdre des points.
- Valeur d'un joueur : valeur par match au-dessus du niveau de remplacement (dernier joueur d'effectif
  de la ligue, 180e pour 15 x 12 ; phase st sur l'horizon court, lt ensuite) x matchs attendus d'ici la
  fin de saison. Les stats réelles restent à l'équipe qui les a faites ; mon lineup est réoptimisé,
  l'autre équipe garde son taux d'alignement. « Matchs alignés (reçus) » : pour chaque joueur reçu,
  matchs que mon lineup optimisé lui donnerait sur ses matchs attendus, avec la répartition horizon
  court / reste de saison (« Cameron Johnson 71/71 (st 0 + lt 71), Moussa Diabate 0/73 (st 0 + lt 0) » :
  le second ne ferait que compléter l'échange ; st reste à 0 si `Optim | Season | Short-term horizon
  (days)` vaut 0) ; « Effet sur mes autres joueurs » : matchs alignés en plus ou en moins pour mes
  joueurs gardés. Colonnes « (pts) » : points roto gagnés ou perdus par catégorie ; colonnes
  « (stats) » : écart de mes stats de fin de saison (FG% / FT% en points de pourcentage, les autres
  en unités : +20,1 REB, -137 PTS).

## Menu du classeur (lancement sans le PC)

Menu **STBL** du classeur (Apps Script, `apps_script/stbl_menu.gs`) -> traitement GitHub Actions
`.github/workflows/stbl-run.yml` -> `python -m scripts.run <code>`. Seuls les codes de `scripts/run.py`
sont acceptés (`python -m scripts.run --list` les affiche avec leurs commandes).

| Menu | Code | Commande |
|---|---|---|
| Projections > Mise à jour | `projections_update` | `python main.py --push-sheet` (CSV des sources copiés depuis Drive) |
| Projections > Recalcul seul | `projections_recalc` | `python main.py --skip-import --push-sheet` |
| Draft > Tirage lottery / Reco / Watch | `lottery_draw` / `draft_reco` / `draft_watch` | `scripts.lottery --draw` / `scripts.draft reco` / `scripts.draft watch` (5 h max, « Arrêter le watch ») |
| Draft > Yahoo Draft / Yahoo Rankings | `yahoo_draft` / `yahoo_rankings` | `scripts.yahoo draft` / `rankings` |
| Yahoo > MàJ rosters / Standings + Team Log | `yahoo_rosters` / `yahoo_stats` | `scripts.yahoo rosters` / `stats` |
| Saison > Projections / Moves / Tout mettre à jour | `season_update` / `season_moves` / `season_all` | `scripts.season update` / `moves` / rosters + stats + update + moves |
| Maintenance > Contrôle settings | `settings_check` | `scripts.config_sheet check` |
| Maintenance > Photo des sources (et chaque matin) | `history_snapshot` | `scripts.history daily` |
| Maintenance > Pertinence des sources | `sources_accuracy` | `scripts.accuracy` |

Chaque lancement ajoute une ligne à l'onglet `runs` : demandé, en cours, puis OK / erreur, durée, lien
vers le log GitHub, dernières lignes en cas d'erreur (aussi : STBL > Maintenance > Dernier lancement).
Résultat en 1 à 3 minutes ; un lancement à la fois (la veille de draft tourne à part).

La base `database.sqlite` et les CSV des sources sont dans le dossier Drive `stbl_data`
(`Sheets | Settings | Drive folder ID`), partagé en Éditeur avec le compte de service : base
téléchargée au début, renvoyée à la fin si elle a changé ; un sous-dossier par source CSV
(`lineupexperts`, `draftkick`, `fanscout`), dont les `.csv` sont copiés dans `data/imports/<source>/`.
Avec `Sheets | Settings | Drive sync = oui`, les commandes lancées sur le PC font de même (la base Drive
fait foi) ; `python -m scripts.cloud pull | push` synchronise à la main.

Mise en place (une fois) :
1. Déposer la base du PC (`database.sqlite`) dans `stbl_data` (le compte de service ne peut que mettre
   à jour un fichier existant), et les CSV du moment dans les sous-dossiers.
2. GitHub, dépôt > Settings > Secrets and variables > Actions > New repository secret :
   `GOOGLE_SERVICE_ACCOUNT` = contenu de `credentials/service_account.json`
   (et `FANTASYNERDS_KEY` si la source est utilisée). « Google Drive API » doit être activée dans la
   console Google Cloud du compte de service.
3. Jeton à accès fin (github.com > Settings > Developer settings > Fine-grained tokens) : dépôt
   `stbl_project` seul, permission « Actions : Read and write ».
4. Classeur > Extensions > Apps Script : coller `apps_script/stbl_menu.gs`, enregistrer, recharger le
   classeur, autoriser le script, puis STBL > Maintenance > Configurer le jeton GitHub.
5. Tester STBL > Maintenance > Contrôle settings, puis Saison > Projections ; ensuite `Drive sync = oui`.

## Photos des sources (history.sqlite)

Pour mesurer en fin de saison (ou à date) la pertinence des sources sur GP, MIN et stats, les projections
et les stats réelles sont photographiées dans `history.sqlite` (dossier Drive `stbl_data`, à côté de la
base). `python -m scripts.history daily`, lancé chaque matin par GitHub (8 h UTC) ou par STBL >
Maintenance > Photo des sources :
1. importe les sources en ligne (étape ros et stats par période, CSV de Drive compris) dans une copie
   temporaire de la base : la base principale, bdd et season_reco ne changent pas ;
2. photographie cette copie, puis la base principale (imports et projections finales draft / lt / st).

Une photo n'est gardée que si son contenu a changé, et au plus une tous les N jours
(`Optim | History`) : stats réelles de la saison à date (sea, résultat de référence) 1 jour ;
projections ros, fenêtres l30 / l15 / l07 et projections finales lt / st 7 jours ; projections draft à
chaque changement. Sa date est celle de l'import ou du calcul des données. Les stats sea d'une autre
saison (plus de matchs joués que de jours écoulés depuis le 1er octobre) sont ignorées.
`Retention (seasons)` : saisons gardées (3). `python -m scripts.history status` : photos disponibles.

## Pertinence des sources (onglet sources_accuracy)

`python -m scripts.accuracy` (ou STBL > Maintenance > Pertinence des sources ; `--no-sheet` : console +
CSV) compare chaque code des grilles draft, lt et st aux stats réelles photographiées, sur les trois
niveaux de la pondération, et propose des poids. Rien n'est réécrit dans les grilles.

- Référence : stats réelles de la saison à date (sea) de la source la plus photographiée.
- Périodes : draft = projections de pré-saison contre la saison jouée ; lt = chaque photo ros (une par
  semaine) contre la suite de la saison ; st = chaque photo des fenêtres l30 / l15 / l07 / sea contre les
  N jours suivants (horizon court terme, 15 jours si le réglage vaut 0). Provisoire tant que la saison
  n'est pas finie ; poids proposés indicatifs sous 25 % des matchs de la période joués.
- Mesures (comme le moteur) : GP = part des matchs de l'équipe jouée, écart en matchs ; MIN = minutes
  par match (3 matchs au moins) ; STATS = production par minute (60 minutes au moins) et FG% / FT%
  pondérés par les tentatives, chaque écart divisé par l'écart-type réel de la catégorie, moyenne des
  catégories (même poids pour toutes, détail par catégorie affiché).
- Mêmes joueurs pour tous : les `Players evaluated` (200) premiers en minutes projetées ou réelles,
  couverts par tous les codes qui en couvrent au moins 80 % ; un code moins couvert est mesuré seul
  (« couverture partielle ») et garde son poids.
- Poids optimal : combinaison de codes (positifs, total 100 %) à la plus petite erreur quadratique ;
  poids proposé = `Shrink to current weights` (50 %) x actuel + le reste x optimal. Lignes « Mélange
  actuel », « Mélange proposé » et « Projection finale (photo) » : erreur de chaque mélange.

## Limites connues

- CBS n'affiche que les 100 premiers joueurs par poste (500 au total) et un seul poste par joueur.
- FantasyPros ne publie les projections `ros` qu'une fois la saison commencée ; en attendant l'import est annulé sans toucher aux données existantes.
- Un import dont une page échoue est entièrement annulé, pour ne jamais remplacer des données complètes par des données partielles.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Couvrent les noms (accents, caractères perdus, alias), les grilles de pondération, les points
roto, l'allocation des matchs sous plafonds et l'aller-retour de l'onglet settings.
