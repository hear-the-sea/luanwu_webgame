from __future__ import annotations

from gameplay.services.virtual_player_core.backfill import request_virtual_player_backfill_for_region_search
from gameplay.services.virtual_player_core.bootstrap import create_virtual_player
from gameplay.services.virtual_player_core.business_metrics import (
    MaintenanceBusinessMetric,
    maintenance_business_metrics_queryset,
    query_maintenance_business_metrics,
)
from gameplay.services.virtual_player_core.config import clear_virtual_player_config_cache, load_virtual_player_config
from gameplay.services.virtual_player_core.contracts import (
    AcceleratedGrowthOutcome,
    BotProjectionConfig,
    PopulationMutationStatus,
)
from gameplay.services.virtual_player_core.maintenance import (
    accelerate_virtual_player_growth,
    maintain_due_virtual_players,
    retire_virtual_player_if_unprotected,
)
from gameplay.services.virtual_player_core.population_runtime import (
    PopulationMutationResult,
    create_virtual_player_with_capacity,
    create_virtual_players_for_band,
    get_virtual_player_capacity,
    plan_virtual_player_population,
    reactivate_retired_virtual_player_with_capacity,
    reactivate_virtual_player_profile,
    roll_virtual_player_population,
    virtual_player_prestige_bands,
)

__all__ = [
    "AcceleratedGrowthOutcome",
    "BotProjectionConfig",
    "MaintenanceBusinessMetric",
    "PopulationMutationResult",
    "PopulationMutationStatus",
    "accelerate_virtual_player_growth",
    "clear_virtual_player_config_cache",
    "create_virtual_player",
    "create_virtual_player_with_capacity",
    "create_virtual_players_for_band",
    "get_virtual_player_capacity",
    "load_virtual_player_config",
    "maintain_due_virtual_players",
    "maintenance_business_metrics_queryset",
    "plan_virtual_player_population",
    "reactivate_retired_virtual_player_with_capacity",
    "reactivate_virtual_player_profile",
    "request_virtual_player_backfill_for_region_search",
    "query_maintenance_business_metrics",
    "retire_virtual_player_if_unprotected",
    "roll_virtual_player_population",
    "virtual_player_prestige_bands",
]
