"""Registre des sources de projections disponibles.

Pour ajouter une source : créer une classe héritant de ProjectionSource
(page web) ou de CsvProjectionSource (fichier CSV),
ajouter son bloc dans config/mappings.json puis l'enregistrer ici.
"""

from scripts.sources.cbs import CbsSource
from scripts.sources.draftkick import DraftKickSource
from scripts.sources.fanscout import FanScoutSource
from scripts.sources.fantasypros import FantasyProsSource

SOURCES = {
    CbsSource.name: CbsSource,
    FantasyProsSource.name: FantasyProsSource,
    FanScoutSource.name: FanScoutSource,
    DraftKickSource.name: DraftKickSource,
}
