"""Backward-compatible re-export of yaml_validators package.

The implementation has been moved to core/utils/yaml_validators/.
This module is kept for backward compatibility only.
"""

from __future__ import annotations

from core.utils.yaml_validators.base import ValidationError, ValidationResult
from core.utils.yaml_validators.gear import (
    validate_forge_blueprints,
    validate_forge_decompose,
    validate_forge_equipment,
    validate_luanwu_shop,
    validate_shop_items,
    validate_smithy_production,
)
from core.utils.yaml_validators.jail_persuasion import validate_jail_persuasion_profiles
from core.utils.yaml_validators.production import (
    validate_guest_growth_rules,
    validate_guest_skills,
    validate_ranch_production,
    validate_stable_production,
    validate_technology_templates,
)
from core.utils.yaml_validators.registry import SUPPORTED_YAML_CONFIGS, get_supported_yaml_configs, validate_all_configs
from core.utils.yaml_validators.rules import (
    validate_arena_coop_rules,
    validate_arena_rewards,
    validate_arena_rules,
    validate_auction_items,
    validate_guild_rules,
    validate_recruitment_rarity_weights,
    validate_trade_market_rules,
    validate_warehouse_production,
)
from core.utils.yaml_validators.templates import (
    validate_building_templates,
    validate_guest_templates,
    validate_guild_mission_templates,
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
    "validate_guild_mission_templates",
    "validate_troop_templates",
    "validate_mission_templates",
    # gear
    "validate_forge_equipment",
    "validate_luanwu_shop",
    "validate_shop_items",
    "validate_forge_blueprints",
    "validate_forge_decompose",
    "validate_smithy_production",
    # rules
    "validate_arena_rules",
    "validate_arena_coop_rules",
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
    "validate_jail_persuasion_profiles",
    # registry
    "validate_all_configs",
    "get_supported_yaml_configs",
    "SUPPORTED_YAML_CONFIGS",
]
