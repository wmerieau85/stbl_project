# Draft : assistant et Yahoo

Détail du fonctionnement. Commandes : voir le [README](../README.md).

## Assistant de draft (Rotisserie)

Le classeur Google Sheets reste l'interface : on saisit le manager de chaque joueur drafté dans la
colonne `Team Draft` (A) de l'onglet `bdd`, sur sa ligne de phase `draft` ; le script en déduit
l'état de la draft (ordre snake, keepers dans leurs tours) et écrit ses recommandations dans
`draft_reco`, que l'onglet `board` (draft board) reprend par formules.

```bash
python main.py --push-sheet                # pipeline complet puis projections (toutes phases) -> bdd
python -m scripts.draft push-projections   # seulement l'envoi des projections vers bdd et bdd_detail
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
   [Configuration](../README.md#configuration)) ; ensuite on ne modifie plus que les onglets.

Projections : `push-projections` écrit toutes les phases de la saison (draft, lt, st, season)
dans l'onglet `bdd`, colonnes C à BK : C = phase, puis la disposition de l'ancien onglet `export`
(joueur en D). Les colonnes A `Team Draft` et B `Team Season` sont des propriétés du joueur :
elles sont conservées et reportées sur toutes ses lignes, même si l'ordre change. Les colonnes à
droite (formules) ne sont pas touchées. Pour une seule phase : `--phase lt`. Onglet et colonne de
départ réglables dans l'onglet settings (section Projections).

Détail par source : `push-projections` (et `python main.py --push-sheet`) écrit aussi l'onglet
`bdd_detail` (réglable : `Sheets | Tab | Detail`, vide = pas d'onglet). Une ligne par joueur et par source (colonne Stage :
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
