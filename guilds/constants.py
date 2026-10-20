"""
帮会系统常量定义

集中管理帮会模块的所有配置常量。
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from core.utils.yaml_loader import ensure_mapping, load_yaml_data

from .technology_tree import (
    GUILD_TECHNOLOGY_CONFIGS,
    TECHNOLOGY_DEFAULT_COSTS,
    TECHNOLOGY_DEFAULT_CURVE_BY_KEY,
    TECHNOLOGY_DEFAULT_DESCRIPTIONS,
    TECHNOLOGY_DEFAULT_NAMES,
)

logger = logging.getLogger(__name__)
GUILD_RULES_PATH = Path(__file__).resolve().parent.parent / "data" / "guild_rules.yaml"

# 同一帮会的同一任务模板每个自然周最多发起次数。
GUILD_MISSION_WEEKLY_LIMIT = 3

DEFAULT_GUILD_RULES: dict[str, Any] = {
    "pagination": {
        "guild_list_page_size": 20,
        "guild_hall_display_limit": 20,
    },
    "creation": {
        "guild_creation_cost": {"gold_bar": 2},
        "guild_upgrade_base_cost": 5,
    },
    "contribution": {
        "units": {"silver": 1000, "grain": 2000, "gold_bar": 1},
        "rates": {"silver": 1, "grain": 1, "gold_bar": 800},
        "daily_limits": {"silver": 500000, "grain": 500000, "gold_bar": 5},
        "troop_rates": {"0": 1, "1": 1, "3": 2, "5": 4, "7": 6},
        "daily_troop_contribution_limit": 300,
        "min_donation_amount": 1,
    },
    "technology": {
        "production_upkeep": {"silver_per_contribution": 60, "grain_per_contribution": 90},
        "upgrade_costs": TECHNOLOGY_DEFAULT_COSTS,
        "upgrade_cost_curves": {
            "standard_10": {
                "2": 2,
                "3": 4,
                "4": 7,
                "5": 12,
                "6": 20,
                "7": 35,
                "8": 70,
                "9": 150,
                "10": 350,
            },
            "welfare_5": {
                "2": 3,
                "3": 8,
                "4": 20,
                "5": 60,
            },
            "research_3": {"2": 3, "3": 6},
            "research_5": {"2": 2, "3": 4, "4": 7, "5": 12},
            "cavalry_10": {"2": 1, "3": 2, "4": 2, "5": 3, "6": 4, "7": 5, "8": 6, "9": 8, "10": 10},
            "capacity_20": {
                "2": 2,
                "3": 3,
                "4": 4,
                "5": 5,
                "6": 6,
                "7": 7,
                "8": 8,
                "9": 9,
                "10": 10,
                "11": 11,
                "12": 12,
                "13": 13,
                "14": 14,
                "15": 15,
                "16": 16,
                "17": 17,
                "18": 18,
                "19": 19,
                "20": 20,
            },
        },
        "upgrade_cost_curve_by_tech": TECHNOLOGY_DEFAULT_CURVE_BY_KEY,
        "upgrade_cost_overrides": {},
        "names": TECHNOLOGY_DEFAULT_NAMES,
        "descriptions": TECHNOLOGY_DEFAULT_DESCRIPTIONS,
    },
    "warehouse": {
        "exchange_costs": {
            "gear_green": 50,
            "gear_blue": 150,
            "gear_purple": 500,
            "gear_orange": 2000,
            "exp_small": 30,
            "exp_medium": 100,
            "exp_large": 400,
            "resource_pack_common": 20,
            "resource_pack_advanced": 80,
        },
        "daily_exchange_limit": 10,
    },
    "cavalry_recruitment": {
        "silver_per_unit": 2000,
        "grain_per_unit": 8000,
        "contribution_per_unit": 3,
        "max_batch": 2000,
        "seconds_per_unit": 30,
    },
    "blueprint_rewards": {"choices": {}},
    "hero_pool": {
        "slot_limit": 2,
        "battle_lineup_limit": 20,
        "dispatch_guest_base_limit": 5,
        "replace_cooldown_seconds": 30 * 60,
    },
    "pvp": {
        "base_travel_time_seconds": 8 * 3600,
        "newbie_protection_seconds": 48 * 3600,
        "defeat_protection_seconds": 12 * 3600,
        "max_daily_attack_count": 2,
        "max_daily_defense_count": 3,
        "max_target_level_gap": 3,
        "silver_floor": 20000,
        "silver_loot_percent": 10,
        "warehouse_loot_percent": 20,
        "fixed_attack_cost_silver": 10000,
        "warehouse_loot_whitelist": ["grain", "gold_bar", "red_ruby"],
    },
}


def _to_positive_int(raw: Any, default: int, *, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = int(default)
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _normalize_int_map(raw: Any, default: dict[str, int], *, minimum: int = 0) -> dict[str, int]:
    result: dict[str, int] = dict(default)
    if not isinstance(raw, dict):
        return result
    for raw_key, raw_value in raw.items():
        key = str(raw_key).strip()
        if not key:
            continue
        result[key] = _to_positive_int(raw_value, default.get(key, minimum), minimum=minimum)
    return result


def _normalize_nested_int_map(raw: Any, default: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {key: dict(value) for key, value in default.items()}
    if not isinstance(raw, dict):
        return result
    for raw_key, raw_value in raw.items():
        key = str(raw_key).strip()
        if not key:
            continue
        fallback = default.get(key, {})
        result[key] = _normalize_int_map(raw_value, fallback, minimum=0)
    return result


def _normalize_upgrade_cost_overrides(
    raw: Any,
    default: dict[str, dict[str, dict[str, int]]],
) -> dict[str, dict[str, dict[str, int]]]:
    result = {
        str(tech_key): {str(level): dict(costs) for level, costs in levels.items()}
        for tech_key, levels in default.items()
    }
    if not isinstance(raw, dict):
        return result

    for raw_tech_key, raw_levels in raw.items():
        tech_key = str(raw_tech_key).strip()
        if not tech_key or not isinstance(raw_levels, dict):
            continue
        normalized_levels = dict(result.get(tech_key, {}))
        for raw_level, raw_costs in raw_levels.items():
            try:
                level = int(raw_level)
            except (TypeError, ValueError):
                continue
            if level < 1:
                continue
            level_key = str(level)
            normalized_levels[level_key] = _normalize_int_map(
                raw_costs,
                normalized_levels.get(level_key, {}),
                minimum=0,
            )
        result[tech_key] = normalized_levels
    return result


def _normalize_string_map(raw: Any, default: dict[str, str]) -> dict[str, str]:
    result = dict(default)
    if not isinstance(raw, dict):
        return result
    for raw_key, raw_value in raw.items():
        key = str(raw_key).strip()
        if not key:
            continue
        result[key] = str(raw_value).strip()
    return result


def normalize_guild_rules(raw: Any) -> dict[str, Any]:
    root = ensure_mapping(raw, logger=logger, context="guild rules root") if raw is not None else {}
    config = {
        "pagination": ensure_mapping(root.get("pagination"), logger=logger, context="guild rules.pagination"),
        "creation": ensure_mapping(root.get("creation"), logger=logger, context="guild rules.creation"),
        "contribution": ensure_mapping(root.get("contribution"), logger=logger, context="guild rules.contribution"),
        "technology": ensure_mapping(root.get("technology"), logger=logger, context="guild rules.technology"),
        "warehouse": ensure_mapping(root.get("warehouse"), logger=logger, context="guild rules.warehouse"),
        "cavalry_recruitment": ensure_mapping(
            root.get("cavalry_recruitment"), logger=logger, context="guild rules.cavalry_recruitment"
        ),
        "blueprint_rewards": ensure_mapping(
            root.get("blueprint_rewards"), logger=logger, context="guild rules.blueprint_rewards"
        ),
        "hero_pool": ensure_mapping(root.get("hero_pool"), logger=logger, context="guild rules.hero_pool"),
        "pvp": ensure_mapping(root.get("pvp"), logger=logger, context="guild rules.pvp"),
    }

    technology_names = _normalize_string_map(
        config["technology"].get("names"),
        DEFAULT_GUILD_RULES["technology"]["names"],
    )
    technology_descriptions = _normalize_string_map(
        config["technology"].get("descriptions"),
        DEFAULT_GUILD_RULES["technology"]["descriptions"],
    )

    return {
        "pagination": {
            "guild_list_page_size": _to_positive_int(
                config["pagination"].get("guild_list_page_size"),
                DEFAULT_GUILD_RULES["pagination"]["guild_list_page_size"],
            ),
            "guild_hall_display_limit": _to_positive_int(
                config["pagination"].get("guild_hall_display_limit"),
                DEFAULT_GUILD_RULES["pagination"]["guild_hall_display_limit"],
            ),
        },
        "creation": {
            "guild_creation_cost": _normalize_int_map(
                config["creation"].get("guild_creation_cost"),
                DEFAULT_GUILD_RULES["creation"]["guild_creation_cost"],
                minimum=0,
            ),
            "guild_upgrade_base_cost": _to_positive_int(
                config["creation"].get("guild_upgrade_base_cost"),
                DEFAULT_GUILD_RULES["creation"]["guild_upgrade_base_cost"],
                minimum=0,
            ),
        },
        "contribution": {
            "units": _normalize_int_map(
                config["contribution"].get("units"),
                DEFAULT_GUILD_RULES["contribution"]["units"],
                minimum=1,
            ),
            "rates": _normalize_int_map(
                config["contribution"].get("rates"),
                DEFAULT_GUILD_RULES["contribution"]["rates"],
                minimum=0,
            ),
            "daily_limits": _normalize_int_map(
                config["contribution"].get("daily_limits"),
                DEFAULT_GUILD_RULES["contribution"]["daily_limits"],
                minimum=0,
            ),
            "troop_rates": _normalize_int_map(
                config["contribution"].get("troop_rates"),
                DEFAULT_GUILD_RULES["contribution"]["troop_rates"],
                minimum=0,
            ),
            "daily_troop_contribution_limit": _to_positive_int(
                config["contribution"].get("daily_troop_contribution_limit"),
                DEFAULT_GUILD_RULES["contribution"]["daily_troop_contribution_limit"],
                minimum=0,
            ),
            "min_donation_amount": _to_positive_int(
                config["contribution"].get("min_donation_amount"),
                DEFAULT_GUILD_RULES["contribution"]["min_donation_amount"],
                minimum=0,
            ),
        },
        "technology": {
            "upgrade_costs": _normalize_nested_int_map(
                config["technology"].get("upgrade_costs"),
                DEFAULT_GUILD_RULES["technology"]["upgrade_costs"],
            ),
            "upgrade_cost_curves": _normalize_nested_int_map(
                config["technology"].get("upgrade_cost_curves"),
                DEFAULT_GUILD_RULES["technology"]["upgrade_cost_curves"],
            ),
            "upgrade_cost_curve_by_tech": _normalize_string_map(
                config["technology"].get("upgrade_cost_curve_by_tech"),
                DEFAULT_GUILD_RULES["technology"]["upgrade_cost_curve_by_tech"],
            ),
            "upgrade_cost_overrides": _normalize_upgrade_cost_overrides(
                config["technology"].get("upgrade_cost_overrides"),
                DEFAULT_GUILD_RULES["technology"]["upgrade_cost_overrides"],
            ),
            "production_upkeep": _normalize_int_map(
                config["technology"].get("production_upkeep"),
                DEFAULT_GUILD_RULES["technology"]["production_upkeep"],
                minimum=0,
            ),
            "names": technology_names,
            "descriptions": technology_descriptions,
        },
        "warehouse": {
            "exchange_costs": _normalize_int_map(
                config["warehouse"].get("exchange_costs"),
                DEFAULT_GUILD_RULES["warehouse"]["exchange_costs"],
                minimum=0,
            ),
            "daily_exchange_limit": _to_positive_int(
                config["warehouse"].get("daily_exchange_limit"),
                DEFAULT_GUILD_RULES["warehouse"]["daily_exchange_limit"],
                minimum=0,
            ),
        },
        "blueprint_rewards": {
            "choices": {
                str(rarity): [str(key).strip() for key in keys if str(key).strip()]
                for rarity, keys in config["blueprint_rewards"].get("choices", {}).items()
                if isinstance(keys, list)
            },
        },
        "cavalry_recruitment": {
            key: _to_positive_int(config["cavalry_recruitment"].get(key), value)
            for key, value in DEFAULT_GUILD_RULES["cavalry_recruitment"].items()
        },
        "hero_pool": {
            "slot_limit": _to_positive_int(
                config["hero_pool"].get("slot_limit"),
                DEFAULT_GUILD_RULES["hero_pool"]["slot_limit"],
                maximum=2,
            ),
            "battle_lineup_limit": _to_positive_int(
                config["hero_pool"].get("battle_lineup_limit"),
                DEFAULT_GUILD_RULES["hero_pool"]["battle_lineup_limit"],
                maximum=20,
            ),
            "dispatch_guest_base_limit": _to_positive_int(
                config["hero_pool"].get("dispatch_guest_base_limit"),
                DEFAULT_GUILD_RULES["hero_pool"]["dispatch_guest_base_limit"],
                maximum=5,
            ),
            "replace_cooldown_seconds": _to_positive_int(
                config["hero_pool"].get("replace_cooldown_seconds"),
                DEFAULT_GUILD_RULES["hero_pool"]["replace_cooldown_seconds"],
                minimum=0,
            ),
        },
        "pvp": {
            "base_travel_time_seconds": _to_positive_int(
                config["pvp"].get("base_travel_time_seconds"),
                DEFAULT_GUILD_RULES["pvp"]["base_travel_time_seconds"],
                minimum=60,
            ),
            "newbie_protection_seconds": _to_positive_int(
                config["pvp"].get("newbie_protection_seconds"),
                DEFAULT_GUILD_RULES["pvp"]["newbie_protection_seconds"],
                minimum=0,
            ),
            "defeat_protection_seconds": _to_positive_int(
                config["pvp"].get("defeat_protection_seconds"),
                DEFAULT_GUILD_RULES["pvp"]["defeat_protection_seconds"],
                minimum=0,
            ),
            "max_daily_attack_count": _to_positive_int(
                config["pvp"].get("max_daily_attack_count"),
                DEFAULT_GUILD_RULES["pvp"]["max_daily_attack_count"],
                minimum=0,
            ),
            "max_daily_defense_count": _to_positive_int(
                config["pvp"].get("max_daily_defense_count"),
                DEFAULT_GUILD_RULES["pvp"]["max_daily_defense_count"],
                minimum=0,
            ),
            "max_target_level_gap": _to_positive_int(
                config["pvp"].get("max_target_level_gap"),
                DEFAULT_GUILD_RULES["pvp"]["max_target_level_gap"],
                minimum=0,
            ),
            "silver_floor": _to_positive_int(
                config["pvp"].get("silver_floor"),
                DEFAULT_GUILD_RULES["pvp"]["silver_floor"],
                minimum=0,
            ),
            "silver_loot_percent": _to_positive_int(
                config["pvp"].get("silver_loot_percent"),
                DEFAULT_GUILD_RULES["pvp"]["silver_loot_percent"],
                minimum=0,
                maximum=100,
            ),
            "warehouse_loot_percent": _to_positive_int(
                config["pvp"].get("warehouse_loot_percent"),
                DEFAULT_GUILD_RULES["pvp"]["warehouse_loot_percent"],
                minimum=0,
                maximum=100,
            ),
            "fixed_attack_cost_silver": _to_positive_int(
                config["pvp"].get("fixed_attack_cost_silver"),
                DEFAULT_GUILD_RULES["pvp"]["fixed_attack_cost_silver"],
                minimum=0,
            ),
            "warehouse_loot_whitelist": [
                str(item_key).strip()
                for item_key in config["pvp"].get(
                    "warehouse_loot_whitelist",
                    DEFAULT_GUILD_RULES["pvp"]["warehouse_loot_whitelist"],
                )
                if str(item_key).strip()
            ]
            or list(DEFAULT_GUILD_RULES["pvp"]["warehouse_loot_whitelist"]),
        },
    }


@lru_cache(maxsize=1)
def load_guild_rules() -> dict[str, Any]:
    raw = load_yaml_data(
        GUILD_RULES_PATH,
        logger=logger,
        context="guild rules config",
        default=DEFAULT_GUILD_RULES,
    )
    return normalize_guild_rules(raw)


def clear_guild_rules_cache() -> None:
    load_guild_rules.cache_clear()


def get_supported_guild_technology_configs() -> tuple[tuple[str, str, int], ...]:
    supported_rule_keys = set(TECH_NAMES).intersection(TECH_UPGRADE_COSTS)
    return tuple(config for config in GUILD_TECHNOLOGY_CONFIGS if config[0] in supported_rule_keys)


def get_supported_guild_technology_keys() -> frozenset[str]:
    return frozenset(tech_key for tech_key, _category, _max_level in get_supported_guild_technology_configs())


def refresh_guild_constants() -> None:
    """重新从 YAML 加载帮会规则并更新模块级常量。"""
    global _GUILD_RULES
    global GUILD_LIST_PAGE_SIZE, GUILD_HALL_DISPLAY_LIMIT
    global GUILD_CREATION_COST, GUILD_UPGRADE_BASE_COST
    global CONTRIBUTION_UNITS, CONTRIBUTION_RATES, DAILY_DONATION_LIMITS, MIN_DONATION_AMOUNT
    global TROOP_CONTRIBUTION_RATES, DAILY_TROOP_CONTRIBUTION_LIMIT
    global TECH_UPGRADE_COSTS, TECH_UPGRADE_COST_CURVES, TECH_UPGRADE_COST_CURVE_BY_TECH
    global TECH_UPGRADE_COST_OVERRIDES, TECH_PRODUCTION_UPKEEP, TECH_NAMES, TECH_DESCRIPTIONS
    global EXCHANGE_COSTS, DAILY_EXCHANGE_LIMIT
    global GUILD_HERO_POOL_SLOT_LIMIT, GUILD_BATTLE_LINEUP_LIMIT, GUILD_DISPATCH_GUEST_BASE_LIMIT
    global GUILD_HERO_POOL_REPLACE_COOLDOWN_SECONDS
    global GUILD_PVP_RULES
    global GUILD_PVP_BASE_TRAVEL_TIME_SECONDS
    global GUILD_PVP_NEWBIE_PROTECTION_SECONDS, GUILD_PVP_DEFEAT_PROTECTION_SECONDS
    global GUILD_PVP_MAX_DAILY_ATTACK_COUNT, GUILD_PVP_MAX_DAILY_DEFENSE_COUNT
    global GUILD_PVP_MAX_TARGET_LEVEL_GAP, GUILD_PVP_SILVER_FLOOR
    global GUILD_PVP_SILVER_LOOT_PERCENT, GUILD_PVP_WAREHOUSE_LOOT_PERCENT
    global GUILD_PVP_FIXED_ATTACK_COST_SILVER, GUILD_PVP_WAREHOUSE_LOOT_WHITELIST

    _GUILD_RULES = load_guild_rules()

    GUILD_LIST_PAGE_SIZE = _GUILD_RULES["pagination"]["guild_list_page_size"]
    GUILD_HALL_DISPLAY_LIMIT = _GUILD_RULES["pagination"]["guild_hall_display_limit"]

    GUILD_CREATION_COST = _GUILD_RULES["creation"]["guild_creation_cost"]
    GUILD_UPGRADE_BASE_COST = _GUILD_RULES["creation"]["guild_upgrade_base_cost"]

    CONTRIBUTION_UNITS = _GUILD_RULES["contribution"]["units"]
    CONTRIBUTION_RATES = _GUILD_RULES["contribution"]["rates"]
    DAILY_DONATION_LIMITS = _GUILD_RULES["contribution"]["daily_limits"]
    TROOP_CONTRIBUTION_RATES = _GUILD_RULES["contribution"]["troop_rates"]
    DAILY_TROOP_CONTRIBUTION_LIMIT = _GUILD_RULES["contribution"]["daily_troop_contribution_limit"]
    MIN_DONATION_AMOUNT = _GUILD_RULES["contribution"]["min_donation_amount"]

    TECH_UPGRADE_COSTS = _GUILD_RULES["technology"]["upgrade_costs"]
    TECH_UPGRADE_COST_CURVES = _GUILD_RULES["technology"]["upgrade_cost_curves"]
    TECH_UPGRADE_COST_CURVE_BY_TECH = _GUILD_RULES["technology"]["upgrade_cost_curve_by_tech"]
    TECH_UPGRADE_COST_OVERRIDES = _GUILD_RULES["technology"]["upgrade_cost_overrides"]
    TECH_PRODUCTION_UPKEEP = _GUILD_RULES["technology"]["production_upkeep"]
    TECH_NAMES = _GUILD_RULES["technology"]["names"]
    TECH_DESCRIPTIONS = _GUILD_RULES["technology"]["descriptions"]

    EXCHANGE_COSTS = _GUILD_RULES["warehouse"]["exchange_costs"]
    DAILY_EXCHANGE_LIMIT = _GUILD_RULES["warehouse"]["daily_exchange_limit"]

    GUILD_HERO_POOL_SLOT_LIMIT = _GUILD_RULES["hero_pool"]["slot_limit"]
    GUILD_BATTLE_LINEUP_LIMIT = _GUILD_RULES["hero_pool"]["battle_lineup_limit"]
    GUILD_DISPATCH_GUEST_BASE_LIMIT = _GUILD_RULES["hero_pool"]["dispatch_guest_base_limit"]
    GUILD_HERO_POOL_REPLACE_COOLDOWN_SECONDS = _GUILD_RULES["hero_pool"]["replace_cooldown_seconds"]
    GUILD_PVP_RULES = _GUILD_RULES["pvp"]
    GUILD_PVP_BASE_TRAVEL_TIME_SECONDS = GUILD_PVP_RULES["base_travel_time_seconds"]
    GUILD_PVP_NEWBIE_PROTECTION_SECONDS = GUILD_PVP_RULES["newbie_protection_seconds"]
    GUILD_PVP_DEFEAT_PROTECTION_SECONDS = GUILD_PVP_RULES["defeat_protection_seconds"]
    GUILD_PVP_MAX_DAILY_ATTACK_COUNT = GUILD_PVP_RULES["max_daily_attack_count"]
    GUILD_PVP_MAX_DAILY_DEFENSE_COUNT = GUILD_PVP_RULES["max_daily_defense_count"]
    GUILD_PVP_MAX_TARGET_LEVEL_GAP = GUILD_PVP_RULES["max_target_level_gap"]
    GUILD_PVP_SILVER_FLOOR = GUILD_PVP_RULES["silver_floor"]
    GUILD_PVP_SILVER_LOOT_PERCENT = GUILD_PVP_RULES["silver_loot_percent"]
    GUILD_PVP_WAREHOUSE_LOOT_PERCENT = GUILD_PVP_RULES["warehouse_loot_percent"]
    GUILD_PVP_FIXED_ATTACK_COST_SILVER = GUILD_PVP_RULES["fixed_attack_cost_silver"]
    GUILD_PVP_WAREHOUSE_LOOT_WHITELIST = GUILD_PVP_RULES["warehouse_loot_whitelist"]


_GUILD_RULES = load_guild_rules()

# ============ 分页与列表 ============
GUILD_LIST_PAGE_SIZE = _GUILD_RULES["pagination"]["guild_list_page_size"]
GUILD_HALL_DISPLAY_LIMIT = _GUILD_RULES["pagination"]["guild_hall_display_limit"]

# ============ 帮会创建与升级 ============
GUILD_CREATION_COST = _GUILD_RULES["creation"]["guild_creation_cost"]
GUILD_UPGRADE_BASE_COST = _GUILD_RULES["creation"]["guild_upgrade_base_cost"]

# ============ 帮会名称校验 ============
GUILD_NAME_MIN_LENGTH = 2
GUILD_NAME_MAX_LENGTH = 12
GUILD_NAME_PATTERN = re.compile(r"^[\u4e00-\u9fa5a-zA-Z0-9_]+$")

# ============ 捐赠系统 ============
CONTRIBUTION_UNITS = _GUILD_RULES["contribution"]["units"]
CONTRIBUTION_RATES = _GUILD_RULES["contribution"]["rates"]
DAILY_DONATION_LIMITS = _GUILD_RULES["contribution"]["daily_limits"]
TROOP_CONTRIBUTION_RATES = _GUILD_RULES["contribution"]["troop_rates"]
DAILY_TROOP_CONTRIBUTION_LIMIT = _GUILD_RULES["contribution"]["daily_troop_contribution_limit"]
MIN_DONATION_AMOUNT = _GUILD_RULES["contribution"]["min_donation_amount"]

# ============ 帮会科技 ============
TECH_UPGRADE_COSTS = _GUILD_RULES["technology"]["upgrade_costs"]
TECH_UPGRADE_COST_CURVES = _GUILD_RULES["technology"]["upgrade_cost_curves"]
TECH_UPGRADE_COST_CURVE_BY_TECH = _GUILD_RULES["technology"]["upgrade_cost_curve_by_tech"]
TECH_UPGRADE_COST_OVERRIDES = _GUILD_RULES["technology"]["upgrade_cost_overrides"]
TECH_PRODUCTION_UPKEEP = _GUILD_RULES["technology"]["production_upkeep"]
TECH_NAMES = _GUILD_RULES["technology"]["names"]
TECH_DESCRIPTIONS = _GUILD_RULES["technology"]["descriptions"]

# ============ 帮会仓库 ============
EXCHANGE_COSTS = _GUILD_RULES["warehouse"]["exchange_costs"]
DAILY_EXCHANGE_LIMIT = _GUILD_RULES["warehouse"]["daily_exchange_limit"]

# ============ 帮会门客池 ============
GUILD_HERO_POOL_SLOT_LIMIT = _GUILD_RULES["hero_pool"]["slot_limit"]
GUILD_BATTLE_LINEUP_LIMIT = _GUILD_RULES["hero_pool"]["battle_lineup_limit"]
GUILD_DISPATCH_GUEST_BASE_LIMIT = _GUILD_RULES["hero_pool"]["dispatch_guest_base_limit"]
GUILD_HERO_POOL_REPLACE_COOLDOWN_SECONDS = _GUILD_RULES["hero_pool"]["replace_cooldown_seconds"]

# ============ 帮会 PVP ============
GUILD_PVP_RULES = _GUILD_RULES["pvp"]
GUILD_PVP_BASE_TRAVEL_TIME_SECONDS = GUILD_PVP_RULES["base_travel_time_seconds"]
GUILD_PVP_NEWBIE_PROTECTION_SECONDS = GUILD_PVP_RULES["newbie_protection_seconds"]
GUILD_PVP_DEFEAT_PROTECTION_SECONDS = GUILD_PVP_RULES["defeat_protection_seconds"]
GUILD_PVP_MAX_DAILY_ATTACK_COUNT = GUILD_PVP_RULES["max_daily_attack_count"]
GUILD_PVP_MAX_DAILY_DEFENSE_COUNT = GUILD_PVP_RULES["max_daily_defense_count"]
GUILD_PVP_MAX_TARGET_LEVEL_GAP = GUILD_PVP_RULES["max_target_level_gap"]
GUILD_PVP_SILVER_FLOOR = GUILD_PVP_RULES["silver_floor"]
GUILD_PVP_SILVER_LOOT_PERCENT = GUILD_PVP_RULES["silver_loot_percent"]
GUILD_PVP_WAREHOUSE_LOOT_PERCENT = GUILD_PVP_RULES["warehouse_loot_percent"]
GUILD_PVP_FIXED_ATTACK_COST_SILVER = GUILD_PVP_RULES["fixed_attack_cost_silver"]
GUILD_PVP_WAREHOUSE_LOOT_WHITELIST = GUILD_PVP_RULES["warehouse_loot_whitelist"]
