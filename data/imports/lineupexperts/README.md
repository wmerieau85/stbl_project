# Exports LineupExperts

Le site étant protégé par Cloudflare, l'export se fait depuis votre navigateur
avec `tools/lineupexperts_export.js` (mode d'emploi : `tools/README.md`).

Le script télécharge directement un fichier au bon format :
`lineupexperts_<étape>_AAAA-MM-JJ.csv` (période « Preseason » = `draft`, sinon `ros`).
Déposez-le ici ; l'import prend automatiquement le plus récent de l'étape active.
Les fichiers CSV de ce dossier ne sont pas versionnés.
