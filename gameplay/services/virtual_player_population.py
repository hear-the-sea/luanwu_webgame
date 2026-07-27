"""Compatibility imports for the relocated pure virtual-player population planner.

Remove this module after all repository and external callers use
``gameplay.services.virtual_player_core.population`` and the compatibility
window for the old import path has ended.
"""

from gameplay.services.virtual_player_core.population import (
    PlannedPopulationCell,
    PopulationCell,
    PopulationPlan,
    plan_population_cells,
)

__all__ = [
    "PlannedPopulationCell",
    "PopulationCell",
    "PopulationPlan",
    "plan_population_cells",
]
