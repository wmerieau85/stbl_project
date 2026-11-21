# Module saison

Détail du fonctionnement. Commandes : voir le [README](../README.md).

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
rate / Opponent ramp (days)`). Seule l'affectation ST/LT de mon équipe est ensuite optimisée face à
elles, sous les plafonds G / F / C / Util : partant du lineup par valeur, une recherche locale teste les
changements un à un (un joueur peu aligné placé devant chaque joueur aligné, ou à sa place, le joueur
remplacé passant en dernier pour que ses matchs restants aillent d'abord aux suivants ; postes
redistribués ; ou un joueur aligné laissé) et garde ceux qui font gagner des points : par exemple laisser
un arrière pour faire jouer en Util un ailier sans place F. Les points sont mesurés exactement comme dans
le classement projeté et les mouvements (points roto espérés, incertitude recalculée avec mon total). L'onglet
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

`python -m scripts.season moves` (menu Saison > Optim Moves ; option `--no-sheet` : console +
`exports/moves_reco_<saison>.csv`) : recherches, lignes 1 à 39 de l'onglet.

`python -m scripts.season simulate` (menu Saison > Simuler un mouvement ; export
`exports/moves_simulation_<saison>.csv`) : un mouvement précis, saisi dans l'onglet settings :

| Ligne | Colonne 2 | Colonne 3 |
|---|---|---|
| `Moves | Team 1` | `Player 1` à `Player 4` | joueurs cédés, tous chez le même manager (n'importe lequel, pas forcément le mien) |
| `Moves | Team 2` | `Player 1` à `Player 4` | joueurs reçus en échange, tous chez un autre manager, ou joueurs libres (dans aucun effectif) |

Le manager de chaque côté est retrouvé d'après ses joueurs ; échange déséquilibré possible (2 contre 1...,
place dans l'effectif non vérifiée). Les deux effectifs sont modifiés : mon lineup est réoptimisé si mon
équipe est concernée, une autre équipe garde son lineup standard (taux d'alignement). Résultat dans
`moves_reco` **à partir de la ligne 40** (Optim Moves n'écrit que les lignes 1 à 39, Simuler un mouvement
que les lignes 40 et suivantes : les deux blocs cohabitent) : 8 lignes d'informations (mouvement, points
et rang de chaque équipe avant / après), en-tête en ligne 48 et le mouvement en ligne 49, dans les mêmes
colonnes que les recherches, du point de vue de Team 1 (« moi » = Team 1, « partenaire » = Team 2 ;
statut gagnant-gagnant ou équipe qui perd des points). « Compléter settings » ajoute les 8 lignes si elles
manquent.

Disposition de l'onglet identique pour tous les types (échanges ou joueurs libres, avec ou sans joueur saisi),
pour qu'un autre onglet puisse le lire par formules : lignes 1 à 8 = informations sur la recherche (hauteur
fixe), en-tête en ligne 9, propositions à partir de la ligne 10, toujours dans ces colonnes :

| A | B | C | D | E | F | G | H | I | J | K | L | M | N | O | P | Q... |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| # | Je cède (ou lâche) | Je reçois (ou ajoute) | Valeur cédée | Valeur reçue | Écart valeur (%) | Gain roto (moi) | Gain roto (partenaire, vide pour les joueurs libres) | Manager (« joueurs libres ») | Statut (gagnant-gagnant, il perd des points, dans / hors tolérance ; vide pour les joueurs libres) | Matchs alignés (reçus) | Effet sur mes autres joueurs | Points projetés | Rang projeté | Titre (%) | Podium (%) | points puis stats par catégorie, puis Valeur perçue cédée / reçue / Écart perçu (%) |
Réglages dans l'onglet settings :

| Ligne | Colonne 2 | Colonne 3 |
|---|---|---|
| `Moves | Player 1` à `4` | manager : le mien (je cède ces joueurs) ou celui de `Moves | Compare` (je les reçois) | joueur (vide = ignoré) |
| `Moves | Compare` | `trade` | nom du manager, ou vide = tous : avec des joueurs dans `Moves | Player`, échange autour d'eux (mes joueurs : avec ce manager, ou vide = recherche auto chez tous les managers où je cède un ou plusieurs de ces joueurs ; ses joueurs : vide = le sien) ; sans joueur, recherche auto d'échanges |
| `Moves | Compare` | `free agent` | (vide) : avec des joueurs (les miens), je les lâche et les meilleurs joueurs libres sont testés à leur place ; sans joueur, recherche auto (qui lâcher, qui prendre) |

Anciens types toujours acceptés : `manager` (= `trade` avec joueurs), `auto` (recherche d'échanges, lignes
`Moves | Player` ignorées), `auto free agent` (recherche de joueurs libres, lignes ignorées).
| `Optim | Moves` | `Value tolerance` | écart de valeur accepté, 10 % par défaut |
| `Optim | Moves` | `Value basis` / `Perceived min games` | `perceived` / 5 : valeur sur laquelle se juge l'équilibre d'un échange. **Perçue** = ce que voient les managers : moyenne réelle de la saison par match (phase `season`) au-dessus du niveau de remplacement × matchs à venir (blessure actuelle comprise ; les matchs déjà manqués ne pénalisent pas un joueur revenu), mélangée à la projection par match sous 5 matchs joués. `projected` = nos projections (ancien comportement). Mon gain reste toujours calculé sur les projections ; les colonnes D-F affichent la valeur projetée, les 3 dernières la valeur perçue. |
| `Optim | Moves` | `Max results` / `Min results` / `Free agent results` / `Free agent shortlist` | 20 / 10 / 6 / 15 |
| `Optim | Moves` | `Auto max players` / `Auto shortlist` / `Auto per manager` / `Auto min partner gain` | 2 / 40 / 5 / 0 |
| `Optim | Moves` | `Min gain` | 0,25 pt roto : gain minimum d'un mouvement affiché par les recherches auto (`auto`, `auto free agent`) ; en dessous, deux lineups presque équivalents (un joueur lâché qui ne jouait pas) donnent un gain sans intérêt |

- Échange : autant de joueurs reçus que cédés. Toutes les combinaisons de l'autre effectif sont
  testées ; à valeur équivalente (tolérance), les 20 meilleurs gains pour moi sont gardés, complétés
  jusqu'à 10 par les combinaisons les plus proches en valeur (« hors tolérance »). Affichage du
  meilleur gain roto pour moi au moins bon, puis, à gain égal (au centième de point), du plus équitable
  au moins équitable, avec le gain de l'autre manager (un échange qui le fait
  perdre a peu de chances d'être accepté), le rang projeté, les chances de titre / podium et l'effet
  par catégorie.
- Recherche auto (`Moves | Compare` = `auto`) : tous les échanges de 1 contre 1 à N contre N
  (`Auto max players`) avec chaque manager sont testés. Filtre de valeur (tolérance), puis crible rapide :
  stats de fin de saison des deux équipes modifiées sans réoptimiser les lineups, en respectant les plafonds
  de matchs par poste : un arrivant ne joue que les matchs libérés par les partants, la place libre, ou
  ceux d'un titulaire moins bon que lui par match (dont les stats sont retirées) ; un remplaçant cédé ne
  libère rien. Points roto recalculés pour moi et pour l'autre manager. Les plus prometteurs, parmi ceux
  qu'il a intérêt à accepter, sont simulés exactement comme un échange classique, par lots de
  `Auto shortlist` répartis entre les tailles d'échange (1 contre 1, 2 contre 2...) et au plus
  2 x `Auto per manager` par manager ; jusqu'à 4 lots tant que `Min results` échanges gagnant-gagnant
  ne sont pas trouvés. Restent ceux qui me font gagner au moins `Min gain` et où son gain est au moins `Auto min partner gain`
  (0 = il ne perd rien) ; un même effet sur mon équipe (mêmes partants qui jouaient, mêmes arrivants
  alignés) n'est montré qu'une fois, dans sa version la plus équitable (le joueur en plus sert alors à
  équilibrer la valeur). Affichage du meilleur gain pour moi au moins bon, puis du plus équitable
  (écart de valeur), au plus `Auto per manager` par manager (ses meilleurs gains) ; complété jusqu'à `Min results` par des échanges où je gagne mais où
  l'autre manager perd des points (colonne Accord : « gagnant-gagnant » / « il perd des points »).
  Principe : à valeur brute équivalente, on échange un surplus dans une catégorie où je suis loin de mes
  voisins de classement contre une catégorie où chaque unité me rapporte des points, l'autre manager ayant
  les besoins inverses. Environ 1 seconde pour 15 équipes en 2 contre 2 ; 3 contre 3 est plus long.
- Joueurs libres (joueurs des projections absents de tous les effectifs Yahoo, statut waivers non
  vérifié) : chacun est testé à la place des joueurs lâchés ; si plusieurs sont lâchés, les 15
  meilleurs sont combinés. Les 6 meilleures options sont affichées, même si elles font perdre des points.
- Joueurs libres, recherche auto (`Moves | Compare` = `auto free agent`) : chacun de mes joueurs (hors
  postes IL : lâcher un blessé ne libère pas de place) contre chacun des 150 meilleurs joueurs libres, au
  même crible rapide que la recherche auto d'échanges. Les plus prometteurs (`Free agent shortlist`, au plus
  2 joueurs à lâcher par joueur libre) sont simulés exactement ; seuls ceux qui me font gagner au moins
  `Min gain` sont affichés, un par joueur libre avec le meilleur joueur à lâcher, du plus grand au plus petit gain
  (au plus `Free agent results`). Pour chaque joueur libre affiché, les autres joueurs à lâcher presque aussi
  bons au crible (à 0,5 pt, 3 au plus) sont simulés aussi : à gain presque égal (0,1 pt, par exemple deux
  remplaçants qui ne jouent pas), on propose de lâcher celui de plus faible valeur (le moins utile en
  remplaçant) et les autres sont cités, « Grayson Allen (ou Cam Spencer, même gain) ». Un joueur libre que mon
  lineup n'alignerait jamais n'est pas proposé (son gain ne viendrait que d'un autre lineup pour mes joueurs). Moins d'une seconde pour 15 équipes.
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

### Analyse draft -> saison à date (season_analysis)

`python -m scripts.season analysis` (menu Saison > Analyse draft / à date ; export
`exports/season_analysis_<saison>.csv`). Pour chaque équipe, chemin en points roto de `draft_reco` aux
standings Yahoo, par étapes qui s'additionnent :

| Étape | Mesure |
|---|---|
| Draft (saison) | projections de la phase draft x matchs prévus par ta rotation de draft (colonne `GP draft` de `bdd`, comme le bloc draft de `board` : même total TOT) ; à défaut, rotation de l'assistant de draft. Points exacts (rang par catégorie, totaux arrondis, comme Yahoo) |
| Calendrier | totaux ramenés à date selon les matchs déjà joués par l'équipe NBA de chaque joueur (= attendu à date) |
| Blessures / matchs | matchs joués (stats réelles, phase season) par rapport aux matchs prévus à la draft |
| Régime | production réelle par match par rapport à la projection de draft, sur les matchs comptés (Team Log) |
| Rotation | gestion du banc pendant la détention : matchs comptés par rapport aux matchs joués chez l'équipe x part prévue à la draft |
| Échanges | joueurs reçus en échange (Team Log), moins les matchs perdus des joueurs draftés échangés (joués après leur départ x part prévue) |
| Joueurs libres | idem pour les joueurs ajoutés / lâchés |
| Reste | non attribué (joueur drafté sans projection...) ; = réel (standings Yahoo, recalculés depuis le Team Log) |

Les étapes sont enchaînées dans cet ordre (en roto les effets se chevauchent). Le détail par joueur donne,
pour chaque axe, les points roto que l'effet a valus à l'équipe (mesurés en le retirant, effets non
additifs). Le bilan des mouvements donne, pour chaque transaction (mon équipe d'abord, puis les autres du
meilleur au pire), les points roto gagnés depuis par rapport à garder le partant : apport de l'arrivant pendant
ce séjour, contre production réelle du partant depuis son départ x part de ses matchs que l'équipe comptait
(bilans non additifs).

Données : onglet `yahoo` (blocs draft, rosters, Team Log et transactions en BJ), phases draft / season de la
base, et pour les matchs joués avant / après chaque transaction les photos quotidiennes des stats de
`history.sqlite` (à défaut, prorata du calendrier NBA, puis des jours). Sans bloc transactions, les matchs non
comptés d'un joueur drafté parti sont classés en joueurs libres, ceux d'un joueur toujours là en rotation, et
il n'y a pas de bilan.
