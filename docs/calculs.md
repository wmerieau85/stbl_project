# Données et calculs

Détail du fonctionnement. Commandes : voir le [README](../README.md).

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

- FG% / FT% finaux : moyenne des pourcentages des sources, pondérée par le poids de la grille × les
  tentatives par match de chaque source ; une source sans match ou à 0 réussite sans tentatives connues
  (« .000 » des pages de stats réelles FantasyPros, souvent 0/0) est ignorée. FGM = FG% × FGA, FTM = FT% × FTA.

## Z-scores

- Groupe de référence = `teams` × taille du roster (15 × 14 = 210 joueurs) : moyenne et
  écart-type sont calculés sur les joueurs réellement draftés, en 3 passes (tous les
  joueurs, puis les 210 meilleurs, etc.).
- `Optim | Draft | Minimum number of games` : matchs minimum pour entrer dans le groupe de référence,
  exprimé pour une saison complète (82 matchs) et ramené aux matchs de chaque phase (seuil × matchs
  maximum de la phase / 82). Exemple avec 20 : 20 en draft, 1 en début de saison pour la phase
  season (3 matchs joués), 2 en fin de saison pour lt (8 matchs restants).
- AVG = moyennes par match (utile en H2H), TOT = totaux sur la saison (Roto, intègre les matchs joués).
- FG% / FT% : z-score de l'impact `(pourcentage - pourcentage du groupe) × tentatives`.
- TO : signe inversé. Somme = Σ poids × z ; rang 1 = meilleure somme.
- Pas encore de rareté par poste (la répartition G/F/C sert seulement à la taille du groupe).

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
