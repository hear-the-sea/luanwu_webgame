"""YAML validators package — schema validation for all game YAML config files."""

from __future__ import annotations

from .base import ValidationError, ValidationResult
from .gear import (
    validate_forge_blueprints,
    validate_forge_decompose,
    validate_forge_equipment,
    validate_luanwu_shop,
    validate_shop_items,
    validate_smithy_production,
)
from .jail_persuasion import validate_jail_persuasion_profiles
from .production import (
    validate_guest_growth_rules,
    validate_guest_skills,
    validate_ranch_production,
    validate_stable_production,
    validate_technology_templates,
)
from .registry import get_supported_yaml_configs, validate_all_configs
from .rules import (
    validate_arena_rewards,
    validate_arena_rules,
    validate_auction_items,
    validate_guild_rules,
    validate_recruitment_rarity_weights,
    validate_trade_market_rules,
    validate_warehouse_production,
)
from .templates import (
    validate_building_templates,
    validate_guest_templates,
    validate_item_templates,
    validate_mission_templates,
    validate_troop_templates,
)

__all__ = [
    # base types
    "ValidationError",
    "ValidationResult",
    # templates
    "validate_item_templates",
    "validate_building_templates",
    "validate_guest_templates",
    "validate_troop_templates",
    "validate_mission_templates",
    # gear
    "validate_forge_equipment",
    "validate_luanwu_shop",
    "validate_shop_items",
    "validate_forge_blueprints",
    "validate_forge_decompose",
    "validate_smithy_production",
    "validate_jail_persuasion_profiles",
    # rules
    "validate_arena_rules",
    "validate_arena_rewards",
    "validate_trade_market_rules",
    "validate_warehouse_production",
    "validate_auction_items",
    "validate_guild_rules",
    "validate_recruitment_rarity_weights",
    # production
    "validate_ranch_production",
    "validate_stable_production",
    "validate_guest_skills",
    "validate_guest_growth_rules",
    "validate_technology_templates",
    # registry
    "validate_all_configs",
    "get_supported_yaml_configs",
]
