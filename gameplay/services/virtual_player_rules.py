from gameplay.services.virtual_player_core.lifecycle import LifecycleDates, choose_lifecycle
from gameplay.services.virtual_player_core.runtime_helpers import (
    DEFAULT_COMBAT_PERSONAS,
    STRENGTH_QUANTILES,
    apply_combat_persona,
    apply_stable_troop_variation,
    bounded_approach,
    choose_strength_quantile,
    nearest_rank_quantile,
)

__all__ = [
    "DEFAULT_COMBAT_PERSONAS",
    "STRENGTH_QUANTILES",
    "LifecycleDates",
    "apply_combat_persona",
    "apply_stable_troop_variation",
    "bounded_approach",
    "choose_lifecycle",
    "choose_strength_quantile",
    "nearest_rank_quantile",
]
