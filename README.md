# STBL – Projections fantasy NBA

Outil d'aide à la décision pour une ligue fantasy NBA Yahoo (Rotisserie) :

1. importe les projections de joueurs de plusieurs sites (web, API, fichiers CSV) dans une base SQLite ;
2. relie les joueurs d'un site à l'autre ;
3. combine les projections avec des poids réglables par site et classe les joueurs (z-scores) ;
4. recommande les choix pendant la draft, puis simule le classement, les échanges et les joueurs libres
   en saison ;
5. se pilote depuis Google Sheets : l'onglet `settings` porte tous les réglages et le menu **STBL** du
   classeur lance les calculs sur GitHub Actions.

Le détail des calculs est dans [`docs/`](docs/) : [données et calculs](docs/calculs.md),
[draft et Yahoo](docs/draft.md), [module saison](docs/saison.md).

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows (source .venv/bin/activate sous Linux/macOS)
pip install -r requirements.txt
```

## Commandes

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
| Maintenance | `python -m pytest -q` | tests (voir [Tests](#tests-et-contrôle-du-code)) |

Étape active (`Actual Phase`) : `draft` n'importe que les sources actives en draft et ne recalcule
que la phase draft ; `ros` importe les projections ros et les stats par période, et ne recalcule
que lt / st. Les autres phases restent telles quelles en base et sont réécrites à l'identique dans
`bdd`. Une phase sans aucune donnée garde son calcul précédent.

Autres options utiles :

```bash
python main.py --stage ros              # force l'étape (sinon Actual Phase)
python main.py --sources fantasypros    # une seule source
python main.py --file fanscout=C:\chemin\table.csv   # fichier CSV précis
python main.py --skip-link -v           # sans rapprochement, logs détaillés
python main.py --skip-yahoo             # sans le pré-classement / ADP Yahoo
python -m scripts.weighting --phase draft   # pondération d'une phase seule
python -m scripts.exports               # réexporte seulement les projections brutes
python -m scripts.sources.cbs           # une source seule
```

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
| `Sources \| Types / Draft / RoS / Active / Import Files / Sea / L30 / L15 / L07` | par code de site (`cbs`, `fp`, `fs`, `dk`, `le`, `nc`, `fn`, `es`, `rb`, `rw`, `dr`, `np`) : URL ou fichier par étape, sources actives par étape, dossiers d'import, fenêtres de stats réelles |
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
python -m scripts.config_sheet complete  # ajoute à l'onglet les paramètres absents (valeur en vigueur + aide)
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
| RotoWire (code `rw`) | API publique Sleeper | automatique, étape RoS : projections RotoWire publiées par [Sleeper](https://api.sleeper.app/projections/nba/2026?season_type=regular) (moyennes par match mises à jour chaque jour, FGM/FGA et FTM/FTA fournis, rookies compris) ; ni matchs ni minutes : stats seulement |
| DARKO (code `dr`) | JSON public | automatique, étape RoS : modèle statistique de [darko.app](https://www.darko.app/projections), mis à jour chaque jour (JSON de la page, format SvelteKit). Stats pour 100 possessions converties par match avec les minutes et le rythme projetés ; tentatives et % fournis ; pas de matchs : minutes et stats |
| Net Prophet (code `np`) | web | automatique, étape RoS : [classement](https://netprophet.pro/rankings) (moyennes par match, 300 joueurs) et [matchs projetés](https://netprophet.pro/games-played) (`Sources \| Players`). Les matchs projetés portent sur la saison complète : ils sont ramenés aux matchs restants avec le calendrier NBA (part des matchs de l'équipe encore à jouer). Joueurs sans match la saison précédente (rookies, absents toute l'année) : valeur par défaut de Net Prophet, matchs non repris. Pas de minutes ni de tentatives (estimées) |
| Fantasy Nerds (code `fn`) | API payante | clé personnelle dans `credentials/fantasynerds_key.txt` (une ligne, ignoré par git) ou variable `FANTASYNERDS_API_KEY` ; projections de draft en totaux, FG% / FT% sans tentatives (estimées). Désactivée par défaut (`Sources | Active`) |

RotoWire, DARKO et Net Prophet sont activés pour le RoS (pas pour la draft, faute d'historique). Ils
entrent dans les photos quotidiennes de `history.sqlite` : l'onglet `sources_accuracy` mesure ensuite leur
précision et propose leurs poids dans la grille lt (`rw.ros`, `dr.ros`, `np.ros`).

ESPN et RotoBaller sont activés pour la draft, pas pour le RoS. RotoBaller publie des moyennes
par match sans nombre de matchs projeté : elles sont donc converties en totaux avec les GP
pondérés des autres sources. Son poids GP reste à 0 % ; ses stats ont 10 % de poids chacune.
Les poids des cinq sources existantes sont réduits proportionnellement pour préserver leurs
poids relatifs. ESPN reçoit 16,67 % du poids GP et des minutes, et 10 % du poids des stats.

Pour les sources CSV, l'import prend le fichier le plus récent de l'étape active
(ou celui passé avec `--file`). Totaux ou moyennes par match : détection
automatique. Les CSV déposés ne sont pas versionnés.

Une source ajoutée au code après la création de l'onglet `settings` (absente de l'onglet) reçoit à la
lecture de l'onglet ses réglages et ses poids livrés (`config/defaults.json`) dans la copie locale ; les
poids des sources déjà configurées sont réduits en gardant leurs proportions. Le classeur n'est pas
modifié : `python -m scripts.config_sheet push` y écrit ces réglages, après vérification de la copie
locale (cette commande réécrit tout l'onglet `settings` et `players`).

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
| Maintenance > Contrôle settings | `settings_check` | `scripts.config_sheet check` (liste aussi les paramètres absents) |
| Maintenance > Compléter settings | `settings_complete` | `scripts.config_sheet complete` : ajoute les paramètres absents de l'onglet (valeur en vigueur + aide) à la fin de leur section |
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
fait foi) ; `python -m scripts.cloud pull | push` synchronise à la main. Si la base Drive a changé
pendant un lancement (un lancement sur le PC et un autre sur GitHub en même temps), la base n'est pas
renvoyée, pour ne pas écraser le travail de l'autre lancement : relancer la commande une fois l'autre
terminé.

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

## Structure

```
main.py                        pipeline : import, rapprochement, pondération, envoi vers le classeur
config/                        bootstrap.json (accès au classeur), defaults.json (configuration livrée)
apps_script/stbl_menu.gs       menu STBL du classeur (lance le workflow GitHub)
.github/workflows/             stbl-run.yml (commandes du menu, photo quotidienne), tests.yml (tests)
tools/                         outils navigateur (export LineupExperts)
data/imports/<source>/         dépôt des exports CSV
docs/                          détail des calculs

scripts/
  config.py                    chemins, chargement et validation de la configuration
  config_sheet.py              onglet settings <-> configuration locale (pull, push, check, complete)
  sheets.py                    lecture / écriture Google Sheets (compte de service)
  cloud.py                     base et CSV partagés sur Google Drive
  run.py                       lanceur des commandes du menu (journal dans l'onglet runs)
  db.py                        schéma SQLite et migrations
  bdd.py                       onglet bdd (colonnes Team Draft / Team Season)
  names.py                     nettoyage des noms et clé de rapprochement
  player_linker.py             table players et rapprochement entre sources
  http_client.py               requêtes (timeout, nouvelles tentatives, pause)
  exports.py                   exports CSV des projections brutes
  history.py                   photos des sources (history.sqlite)
  accuracy.py                  pertinence des sources (onglet sources_accuracy)
  lottery.py                   tirage au sort de l'ordre de draft
  sources/                     une classe par site ; base.py (logique commune), csv_source.py (sources CSV)
  weighting/                   grilles de pondération (weights.py), projections finales (engine.py),
                               z-scores (zscores.py)
  draft/                       assistant de draft : état (state.py), simulations (engine.py),
                               joueurs (pool.py), onglets bdd / bdd_detail / draft_reco
  season/                      module saison : projection des équipes (engine.py), adversaires
                               (opponents.py), calendrier (schedule.py), échanges (moves.py)
  yahoo/                       API Yahoo (client.py, league.py), pages publiques (public.py),
                               pré-classement (rankings.py), onglet yahoo (sheets.py)
```

## Ajouter une source

- **web** : une classe héritant de `ProjectionSource` (méthodes `page_requests` et `parse_page`) ;
- **CSV** : une classe de deux lignes héritant de `CsvProjectionSource` (voir `fanscout.py`) ;
  tout le reste se règle dans l'onglet settings (sections Sources et Transco) :
  - `import_dir`, `files` (motif du fichier par étape) ;
  - `player_column`, `team_column`, `positions_column` (un en-tête en double devient `Nom_2`) ;
  - `stat_mode` (`auto`, `totals`, `per_game`), `percent_scale` (1 ou 100) ;
  - `empty_as_zero` (case vide = 0), `missing_values` (ex. ADP 999 = vide), `skip_players` ;
  - `columns` (en-tête du CSV -> colonne standard) ;

puis sa configuration dans `config/defaults.json` > `sources` (code, `urls` ou `files`) et une ligne dans `scripts/sources/__init__.py`.

## Limites connues

- CBS n'affiche que les 100 premiers joueurs par poste (500 au total) et un seul poste par joueur.
- FantasyPros ne publie les projections `ros` qu'une fois la saison commencée ; en attendant l'import est annulé sans toucher aux données existantes.
- Un import dont une page échoue est entièrement annulé, pour ne jamais remplacer des données complètes par des données partielles.

## Tests et contrôle du code

```bash
pip install -r requirements-dev.txt
python -m pytest -q      # tests
ruff check .             # contrôle du code (imports inutilisés, noms inconnus...), réglé dans ruff.toml
```

Les deux sont lancés par GitHub (workflow `tests.yml`) à chaque pull request et à chaque envoi sur `main`.
