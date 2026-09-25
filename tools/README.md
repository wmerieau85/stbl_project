# Outils navigateur

## Export LineupExperts (`lineupexperts_export.js`)

LineupExperts est protégé par Cloudflare et n'offre ni CSV ni tableau complet
(100 joueurs par page au maximum). Ce script s'exécute dans **votre** navigateur,
sur la page déjà ouverte, lit **toutes** les lignes du tableau (toutes pages
confondues) et télécharge `lineupexperts_<étape>_AAAA-MM-JJ.csv`, prêt à être
déposé dans `data/imports/lineupexperts/`.

### Option 1 : favori (recommandé)

1. Afficher la barre de favoris (Ctrl+Maj+B).
2. Clic droit sur la barre > « Ajouter une page » (Chrome) / « Ajouter un favori » (Edge).
3. Nom : `STBL - Export LineupExperts` ; URL : coller **tout** le contenu de
   `lineupexperts_bookmarklet.txt` (une seule ligne commençant par `javascript:`).
4. Ouvrir https://www.lineupexperts.com/basketball/projections?flt_proj_time_period=Preseason,
   attendre l'affichage du tableau, puis cliquer sur le favori.

### Option 2 : console

Sur la page ouverte : F12 > onglet Console > coller le contenu de
`lineupexperts_export.js` > Entrée. (Chrome demande parfois de taper
`allow pasting` avant le premier collage.)

### Ensuite

Déplacer le fichier téléchargé dans `data/imports/lineupexperts/`, puis
`python main.py`. Période « Preseason » = étape `draft` ; toute autre période
(ex. « Rest of Season ») = `ros`.

Si vous modifiez `lineupexperts_export.js`, régénérez le favori :
`npx terser tools/lineupexperts_export.js -c -m --format ascii_only=true`,
puis préfixez le résultat par `javascript:`.
