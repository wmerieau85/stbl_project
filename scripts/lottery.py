"""Tirage au sort de l'ordre de draft : génération des boules (bloc lottery de l'onglet board).

Plages lues (onglet settings, sections Draft Lottery) :
- Managers       : liste des managers (board!B8:B22) ;
- Active         : 0 = manager encore dans le tirage, sinon déjà placé dans l'ordre final (board!L8:L22) ;
- Number Balls   : boules par manager, 1re colonne = tirage 1, 2e colonne = tirage 2 (board!N8:O22) ;
- Lottery Number : tirage en cours, 1 ou 2 (board!AA9).

Les managers encore dans le tirage reçoivent leur nombre de boules ; les boules sont numérotées
de 1 à N et mélangées, puis écrites (Ball | Manager) dans la plage Balls (par défaut lottery!A1,
onglet créé s'il n'existe pas). Une formule du board peut alors retrouver le manager d'une boule
tirée, par ex. =VLOOKUP(AA17; lottery!A:B; 2; FALSE).

    python -m scripts.lottery                 # génère et écrit les boules
    python -m scripts.lottery --draw          # ... puis tire une boule au hasard et affiche le manager
    python -m scripts.lottery --seed 42       # tirage reproductible (contrôle)
    python -m scripts.lottery --dry-run       # affiche sans écrire
"""

import argparse
import logging
import random
import sys

log = logging.getLogger(__name__)


def _number(value):
    text = str(value if value is not None else "").strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
    if not text:
        return 0.0
    try:
        return float(text.rstrip("%"))
    except ValueError:
        return None


def generate_balls(managers, active, balls, lottery_number, rng=None):
    """[(numéro de boule, manager)] pour les managers encore dans le tirage (active = 0).

    managers : [nom] ; active : [valeur] ; balls : [[tirage 1, tirage 2]] ; lottery_number : 1 ou 2.
    """
    if lottery_number not in (1, 2):
        raise ValueError(f"Draft Lottery | Lottery Number = {lottery_number!r} : 1 ou 2 attendu.")
    column = lottery_number - 1
    pot = []
    for i, manager in enumerate(managers):
        manager = str(manager or "").strip()
        if not manager:
            continue
        state = _number(active[i] if i < len(active) else "")
        if state is None or state != 0:
            continue
        row = balls[i] if i < len(balls) else []
        count = _number(row[column] if column < len(row) else "")
        if count is None or count < 0 or count != int(count):
            raise ValueError(f"Draft Lottery | Number Balls : valeur illisible pour {manager} ({row}).")
        pot += [manager] * int(count)
    if not pot:
        raise ValueError("Aucune boule à générer : aucun manager encore dans le tirage avec des boules.")
    (rng or random.SystemRandom()).shuffle(pot)
    return list(enumerate(pot, 1))


def summary(balls):
    counts = {}
    for _, manager in balls:
        counts[manager] = counts.get(manager, 0) + 1
    total = len(balls)
    return [(m, n, n / total) for m, n in sorted(counts.items(), key=lambda x: -x[1])]


def _values(book, ranges):
    data = book.values_batch_get(ranges).get("valueRanges", [])
    return [d.get("values", []) for d in data]


def run(book, league, draw=False, seed=None, dry_run=False):
    from scripts.sheets import write_block

    conf = league.get("lottery", {})
    keys = ("managers_range", "active_range", "balls_range", "number_range")
    missing = [k for k in keys if not conf.get(k)]
    if missing:
        raise ValueError(f"Plages Draft Lottery absentes de l'onglet settings : {', '.join(missing)}.")
    managers, active, balls, number = _values(book, [conf[k] for k in keys])
    flat = lambda rows: [r[0] if r else "" for r in rows]  # noqa: E731
    lottery_number = _number(flat(number)[0] if number else "")
    rng = random.Random(seed) if seed is not None else None
    result = generate_balls(flat(managers), flat(active), balls, int(lottery_number or 0), rng)

    print(f"Tirage {int(lottery_number)} : {len(result)} boules.")
    for manager, n, share in summary(result):
        print(f"  {manager:<10} {n:>5} boules  {share:6.1%}")
    if not dry_run:
        target = conf.get("output_range") or "lottery!A1"
        tab, _, cell = target.partition("!")
        first_col = "".join(c for c in (cell or "A1") if c.isalpha()) or "A"
        write_block(book, tab, [["Ball", "Manager"]] + [[b, m] for b, m in result], first_col=first_col)
        print(f"Boules écrites dans {tab} (colonnes {first_col} et suivante).")
    if draw:
        ball, manager = (rng or random.SystemRandom()).choice(result)
        print(f"Boule tirée : n°{ball} -> {manager}")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Génération des boules du tirage au sort de la draft")
    parser.add_argument("--draw", action="store_true", help="tirer une boule au hasard après la génération")
    parser.add_argument("--seed", type=int, help="graine du hasard (tirage reproductible)")
    parser.add_argument("--dry-run", action="store_true", help="ne rien écrire dans le classeur")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    from scripts import config_sheet
    from scripts.config import load_league, load_settings
    from scripts.sheets import open_spreadsheet

    league = load_league()
    gs = league["google_sheets"]
    book = open_spreadsheet(gs["draft_spreadsheet_id"], load_settings())
    try:
        league = config_sheet.pull(book, gs.get("config_tab"))
        run(book, league, draw=args.draw, seed=args.seed, dry_run=args.dry_run)
    except ValueError as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
