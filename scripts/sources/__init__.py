"""Registre des sources de projections disponibles.

Pour ajouter une source : créer une classe héritant de ProjectionSource
(page web) ou de CsvProjectionSource (fichier CSV),
ajouter sa configuration (config/defaults.json > sources, puis onglet settings) et l'enregistrer ici.
"""

from scripts.sources.cbs import CbsSource
from scripts.sources.draftkick import DraftKickSource
from scripts.sources.espn import ESPNSource
from scripts.sources.fanscout import FanScoutSource
from scripts.sources.fantasynerds import FantasyNerdsSource
from scripts.sources.fantasypros import FantasyProsSource
from scripts.sources.lineupexperts import LineupExpertsSource
from scripts.sources.ninecat import NineCatSource
from scripts.sources.rotoballer import RotoBallerSource

SOURCES = {
    CbsSource.name: CbsSource,
    FantasyProsSource.name: FantasyProsSource,
    ESPNSource.name: ESPNSource,
    FanScoutSource.name: FanScoutSource,
    DraftKickSource.name: DraftKickSource,
    LineupExpertsSource.name: LineupExpertsSource,
    NineCatSource.name: NineCatSource,
    FantasyNerdsSource.name: FantasyNerdsSource,
    RotoBallerSource.name: RotoBallerSource,
}
