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
| `Moves | Compare` | `auto` | (vide = tous les managers, ou un nom de manager) : recherche d'échanges sans joueur saisi, lignes `Moves | Player` ignorées |
| `Moves | Compare` | `auto free agent` | (vide) : recherche de joueurs libres sans joueur saisi (qui lâcher, qui prendre), lignes `Moves | Player` ignorées |
| `Optim | Moves` | `Value tolerance` | écart de valeur accepté, 10 % par défaut |
| `Optim | Moves` | `Max results` / `Min results` / `Free agent results` / `Free agent shortlist` | 20 / 10 / 6 / 15 |
| `Optim | Moves` | `Auto max players` / `Auto shortlist` / `Auto per manager` / `Auto min partner gain` | 2 / 40 / 5 / 0 |

- Échange : autant de joueurs reçus que cédés. Toutes les combinaisons de l'autre effectif sont
  testées ; à valeur équivalente (tolérance), les 20 meilleurs gains pour moi sont gardés, complétés
  jusqu'à 10 par les combinaisons les plus proches en valeur (« hors tolérance »). Affichage du plus
  équitable au moins équitable, avec le gain de l'autre manager (un échange qui le fait
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
  ne sont pas trouvés. Restent ceux qui me font gagner et où son gain est au moins `Auto min partner gain`
  (0 = il ne perd rien) ; un même effet sur mon équipe (mêmes partants qui jouaient, mêmes arrivants
  alignés) n'est montré qu'une fois, dans sa version la plus équitable (le joueur en plus sert alors à
  équilibrer la valeur). Affichage du plus équitable au moins équitable (écart de valeur), au plus
  `Auto per manager` par manager ; complété jusqu'à `Min results` par des échanges où je gagne mais où
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
  2 joueurs à lâcher par joueur libre) sont simulés exactement ; seuls ceux qui me font gagner des points
  sont affichés, un par joueur libre avec le meilleur joueur à lâcher, du plus grand au plus petit gain
  (au plus `Free agent results`). Moins d'une seconde pour 15 équipes.
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
