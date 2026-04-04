"""High-level registry: validate all YAML configs at once."""

from __future__ import annotations

import logging
from pathlib import Path

from core.utils.yaml_loader import load_yaml_data

from .base import ValidationResult
from .gear import (
    validate_forge_blueprints,
    validate_forge_decompose,
    validate_forge_equipment,
    validate_shop_items,
    validate_smithy_production,
)
from .production import (
    validate_guest_growth_rules,
    validate_guest_skills,
    validate_ranch_production,
    validate_stable_production,
    validate_technology_templates,
)
from .rules import (
    validate_arena_coop_rules,
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
    validate_guild_mission_templates,
    validate_item_templates,
    validate_mission_templates,
    validate_troop_templates,
)

logger = logging.getLogger(__name__)

SUPPORTED_YAML_CONFIGS = (
    "item_templates.yaml",
    "building_templates.yaml",
    "guest_templates.yaml",
    "troop_templates.yaml",
    "mission_templates.yaml",
    "forge_equipment.yaml",
    "shop_items.yaml",
    "arena_rules.yaml",
    "arena_coop_rules.yaml",
    "trade_market_rules.yaml",
    "warehouse_production.yaml",
    "auction_items.yaml",
    "forge_blueprints.yaml",
    "forge_decompose.yaml",
    "guest_skills.yaml",
    "arena_coop_special_skills.yaml",
    "recruitment_rarity_weights.yaml",
    "arena_rewards.yaml",
    "smithy_production.yaml",
    "ranch_production.yaml",
    "stable_production.yaml",
    "guild_rules.yaml",
    "guest_growth_rules.yaml",
    "technology_templates.yaml",
    "guild_mission_templates.yaml",
)


def validate_all_configs(data_dir: str | Path) -> ValidationResult:
    """Load and validate all YAML config files from the data directory."""
    data_path = Path(data_dir)
    result = ValidationResult()

    def _load(filename: str) -> dict | None:
        filepath = data_path / filename
        if not filepath.exists():
            result.add(filename, "<file>", f"file not found: {filepath}")
            return None
        data = load_yaml_data(filepath, logger=logger, context=f"validate {filename}", default=None)
        if data is None:
            result.add(filename, "<file>", "failed to load YAML or file is empty")
            return None
        return data

    # Load all configs
    item_data = _load("item_templates.yaml")
    building_data = _load("building_templates.yaml")
    guest_data = _load("guest_templates.yaml")
    troop_data = _load("troop_templates.yaml")
    mission_data = _load("mission_templates.yaml")
    forge_data = _load("forge_equipment.yaml")
    shop_data = _load("shop_items.yaml")
    arena_data = _load("arena_rules.yaml")
    arena_coop_data = _load("arena_coop_rules.yaml")
    trade_data = _load("trade_market_rules.yaml")
    warehouse_data = _load("warehouse_production.yaml")
    auction_data = _load("auction_items.yaml")
    blueprints_data = _load("forge_blueprints.yaml")
    decompose_data = _load("forge_decompose.yaml")
    skills_data = _load("guest_skills.yaml")
    arena_coop_skills_data = _load("arena_coop_special_skills.yaml")
    rarity_weights_data = _load("recruitment_rarity_weights.yaml")
    arena_rewards_data = _load("arena_rewards.yaml")
    smithy_data = _load("smithy_production.yaml")
    ranch_data = _load("ranch_production.yaml")
    stable_data = _load("stable_production.yaml")
    guild_data = _load("guild_rules.yaml")
    growth_data = _load("guest_growth_rules.yaml")
    tech_data = _load("technology_templates.yaml")
    guild_mission_data = _load("guild_mission_templates.yaml")

    # Build cross-reference key sets for referential integrity checks
    item_keys: set[str] | None = None
    if item_data is not None:
        result.merge(validate_item_templates(item_data))
        items_list = item_data.get("items") or []
        if isinstance(items_list, list):
            item_keys = {str(item["key"]) for item in items_list if isinstance(item, dict) and item.get("key")}

    troop_keys: set[str] | None = None
    if troop_data is not None:
        result.merge(validate_troop_templates(troop_data))
        troops_list = troop_data.get("troops") or []
        if isinstance(troops_list, list):
            troop_keys = {str(troop["key"]) for troop in troops_list if isinstance(troop, dict) and troop.get("key")}

    if building_data is not None:
        result.merge(validate_building_templates(building_data))

    if guest_data is not None:
        result.merge(validate_guest_templates(guest_data))

    if mission_data is not None:
        result.merge(validate_mission_templates(mission_data, item_keys=item_keys, troop_keys=troop_keys))

    if forge_data is not None:
        result.merge(validate_forge_equipment(forge_data, item_keys=item_keys))

    if shop_data is not None:
        result.merge(validate_shop_items(shop_data, item_keys=item_keys))

    if arena_data is not None:
        result.merge(validate_arena_rules(arena_data))

    if arena_coop_data is not None:
        result.merge(validate_arena_coop_rules(arena_coop_data))

    if trade_data is not None:
        result.merge(validate_trade_market_rules(trade_data))

    if warehouse_data is not None:
        result.merge(validate_warehouse_production(warehouse_data))

    if auction_data is not None:
        result.merge(validate_auction_items(auction_data, item_keys=item_keys))

    if blueprints_data is not None:
        result.merge(validate_forge_blueprints(blueprints_data, item_keys=item_keys))

    if decompose_data is not None:
        result.merge(validate_forge_decompose(decompose_data))

    if skills_data is not None:
        result.merge(validate_guest_skills(skills_data))

    if arena_coop_skills_data is not None:
        result.merge(validate_guest_skills(arena_coop_skills_data, file="arena_coop_special_skills.yaml"))

    if rarity_weights_data is not None:
        result.merge(validate_recruitment_rarity_weights(rarity_weights_data))

    if arena_rewards_data is not None:
        result.merge(validate_arena_rewards(arena_rewards_data, item_keys=item_keys))

    if smithy_data is not None:
        result.merge(validate_smithy_production(smithy_data))

    if ranch_data is not None:
        result.merge(validate_ranch_production(ranch_data))

    if stable_data is not None:
        result.merge(validate_stable_production(stable_data, item_keys=item_keys))

    if guild_data is not None:
        result.merge(validate_guild_rules(guild_data))

    if growth_data is not None:
        result.merge(validate_guest_growth_rules(growth_data))

    if tech_data is not None:
        result.merge(validate_technology_templates(tech_data))

    if guild_mission_data is not None:
        result.merge(validate_guild_mission_templates(guild_mission_data))

    return result


def get_supported_yaml_configs() -> tuple[str, ...]:
    """Return the YAML config filenames currently covered by schema validation."""
    return SUPPORTED_YAML_CONFIGS
