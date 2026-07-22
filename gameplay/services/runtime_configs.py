from __future__ import annotations

from typing import Any


def reload_runtime_configs() -> dict[str, int]:
    from gameplay.services.arena.coop_core import refresh_arena_coop_constants
    from gameplay.services.arena.coop_rules import clear_arena_coop_rules_cache, load_arena_coop_rules
    from gameplay.services.arena.core import refresh_arena_constants
    from gameplay.services.arena.rewards import clear_arena_reward_cache, load_arena_reward_catalog
    from gameplay.services.arena.rules import clear_arena_rules_cache, load_arena_rules
    from gameplay.services.buildings.forge import (
        clear_forge_blueprint_cache,
        clear_forge_decompose_cache,
        clear_forge_equipment_cache,
        load_forge_blueprint_config,
        load_forge_decompose_config,
        load_forge_equipment_config,
    )
    from gameplay.services.buildings.ranch import clear_ranch_production_cache, load_ranch_production_config
    from gameplay.services.buildings.smithy import clear_smithy_production_cache, load_smithy_production_config
    from gameplay.services.buildings.stable import clear_stable_production_cache, load_stable_production_config
    from gameplay.services.jail_persuasion.profiles import (
        clear_jail_persuasion_profiles_cache,
        load_jail_persuasion_profiles,
    )
    from gameplay.services.virtual_players import clear_virtual_player_config_cache, load_virtual_player_config
    from guests.growth_rules import clear_guest_growth_rules_cache, load_guest_growth_rules
    from guests.utils.recruitment_utils import refresh_recruitment_rarity_constants
    from guilds.constants import clear_guild_rules_cache, load_guild_rules, refresh_guild_constants
    from guilds.services.warehouse_config import get_warehouse_production, reload_warehouse_production
    from trade.services.auction_config import load_auction_config, reload_auction_config
    from trade.services.market_service import clear_trade_market_rules_cache, load_trade_market_rules
    from trade.services.shop_config import load_shop_config, reload_shop_config

    reload_shop_config()
    shop_items = load_shop_config()

    reload_auction_config()
    auction_config = load_auction_config()

    reload_warehouse_production()
    warehouse_cfg = get_warehouse_production()

    clear_forge_equipment_cache()
    forge_equipment_cfg = load_forge_equipment_config()

    clear_forge_blueprint_cache()
    blueprint_cfg = load_forge_blueprint_config()

    clear_forge_decompose_cache()
    decompose_cfg = load_forge_decompose_config()

    clear_stable_production_cache()
    stable_cfg = load_stable_production_config()

    clear_ranch_production_cache()
    ranch_cfg = load_ranch_production_config()

    clear_smithy_production_cache()
    smithy_cfg = load_smithy_production_config()

    clear_guest_growth_rules_cache()
    guest_growth_rules = load_guest_growth_rules()
    _recruitment_total_weight, recruitment_weights, _recruitment_distribution = refresh_recruitment_rarity_constants()

    clear_arena_reward_cache()
    arena_rewards = load_arena_reward_catalog()

    clear_arena_rules_cache()
    arena_rules = load_arena_rules()
    refresh_arena_constants()

    clear_arena_coop_rules_cache()
    arena_coop_rules = load_arena_coop_rules()
    refresh_arena_coop_constants()

    clear_trade_market_rules_cache()
    trade_market_rules = load_trade_market_rules()

    clear_guild_rules_cache()
    guild_rules = load_guild_rules()
    refresh_guild_constants()

    clear_virtual_player_config_cache()
    virtual_players = load_virtual_player_config()

    clear_jail_persuasion_profiles_cache()
    jail_persuasion = load_jail_persuasion_profiles()

    return {
        "shop_items": len(shop_items),
        "auction_items": len(getattr(auction_config, "items", [])),
        "warehouse_techs": len(warehouse_cfg),
        "forge_equipment": len(forge_equipment_cfg),
        "forge_blueprints": len(blueprint_cfg.get("recipes", []) or []),
        "forge_decompose_rarities": len(decompose_cfg.get("supported_rarities", []) or []),
        "stable_entries": len(stable_cfg),
        "ranch_entries": len(ranch_cfg),
        "smithy_entries": len(smithy_cfg),
        "guest_growth_rarities": len((guest_growth_rules.get("rarity_attribute_growth_range") or {})),
        "recruitment_rarity_weights": len(recruitment_weights),
        "arena_rewards": len(arena_rewards),
        "arena_rank_rules": len((arena_rules.get("rewards") or {}).get("rank_bonus_coins", {})),
        "arena_coop_rank_rules": len((arena_coop_rules.get("rewards") or {}).get("rank_rewards", {})),
        "trade_listing_durations": len((trade_market_rules.get("listing_fees") or {})),
        "guild_tech_rules": len((guild_rules.get("technology") or {}).get("upgrade_costs", {})),
        "virtual_players": len((virtual_players.get("prestige_bands") or {})),
        "jail_persuasion_methods": len((jail_persuasion.get("methods") or {})),
    }


def format_runtime_config_summary(summary: dict[str, Any]) -> str:
    ordered_keys = [
        "shop_items",
        "auction_items",
        "warehouse_techs",
        "forge_equipment",
        "forge_blueprints",
        "forge_decompose_rarities",
        "stable_entries",
        "ranch_entries",
        "smithy_entries",
        "guest_growth_rarities",
        "recruitment_rarity_weights",
        "arena_rewards",
        "arena_rank_rules",
        "arena_coop_rank_rules",
        "trade_listing_durations",
        "guild_tech_rules",
        "virtual_players",
        "jail_persuasion_methods",
    ]
    parts = [f"{key}={summary[key]}" for key in ordered_keys if key in summary]
    return ", ".join(parts)
