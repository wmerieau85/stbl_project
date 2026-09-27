# Grilles de pondération

Un fichier par phase : `draft.csv` (pré-saison), plus tard `lt.csv` (long terme, ROS)
et `st.csv` (court terme, forme récente). Les phases calculées pour chaque étape sont
définies dans `settings.json` > `phases` (par défaut `draft` → `draft`, `ros` → `lt` et `st`).

## Format

La grille telle qu'elle est dans Google Sheets (copier/coller dans le fichier, ou
export CSV). Séparateur `;`, `,` ou tabulation. Poids en `22,50%`, `22.5%`, `0.225` ou `22.5`.

```
;lt01;lt02;lt03;lt04;lt05
site;fs26;fp26;cbs26;le26;dk26
GP;22,50%;22,50%;10,00%;22,50%;22,50%
MIN;20,00%;20,00%;20,00%;20,00%;20,00%
STATS;20,00%;20,00%;20,00%;20,00%;20,00%
```

- Chaque ligne GP / MIN / STATS doit totaliser 100 % (tolérance 0,5 %).
- Ligne optionnelle pour une stat précise, ex. `FT%` ou `BLK` (ou `STATS:BLK`) :
  elle remplace la ligne STATS pour cette stat seulement, et doit aussi faire 100 %.
  Libellés reconnus : PTS, REB, AST, STL, BLK, TO, 3PM, FGA, FTA, 3PA, FG%, FT%.
- Un emplacement vide ou à 0 % partout est ignoré.

## Codes « site »

| Code | Signification |
|---|---|
| `fs26` | FanScout, saison 2026-27, projections de pré-saison (draft) |
| `fp.ros` | FantasyPros, saison active, rest of season |
| `fp26.ros` | FantasyPros, saison 2026-27, rest of season |
| `fp.l30` | FantasyPros, fenêtre 30 derniers jours (quand la source sera importée) |
| `draft` | projections finales de la phase draft (utilisable dans `lt` / `st`) |

Codes des sites (clé `code` dans `mappings.json`) : `cbs`, `fp` (FantasyPros),
`fs` (FanScout), `dk` (DraftKick), `le` (LineupExperts).

## Calcul

Total d'une stat = **GP** × **MPG** × **production par minute**, chaque terme étant la
moyenne pondérée par sa ligne. Source sans minutes (DraftKick) : sa moyenne par match
est prise telle quelle. FG% et FT% sont des moyennes pondérées ; FGM = FG% × FGA et
FTM = FT% × FTA. Quand un joueur ou une stat manque dans une source, les poids des
autres sources sont renormalisés (colonnes `*_coverage` = part du poids présente).
Sans aucune source de tentatives, FGA/FTA sont estimés (`fga_estimated = 1`).

Résultat : table `final_projections` et `exports/final_<phase>_<saison>.csv`.
Recalcul seul, sans réimport : `python main.py --skip-import` ou `python -m scripts.weighting`.
