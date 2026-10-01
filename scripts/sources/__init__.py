"""Registre des sources de projections disponibles.

Pour ajouter une source : créer une classe héritant de ProjectionSource
(page web) ou de CsvProjectionSource (fichier CSV),
ajouter sa configuration (config/defaults.json > sources, puis onglet config) et l'enregistrer ici.
"""

from scripts.sources.cbs import CbsSource
from scripts.sources.draftkick import DraftKickSource
from scripts.sources.fanscout import FanScoutSource
from scripts.sources.fantasypros import FantasyProsSource
from scripts.sources.lineupexperts import LineupExpertsSource

SOURCES = {
    CbsSource.name: CbsSource,
    FantasyProsSource.name: FantasyProsSource,
    FanScoutSource.name: FanScoutSource,
    DraftKickSource.name: DraftKickSource,
    LineupExpertsSource.name: LineupExpertsSource,
}
