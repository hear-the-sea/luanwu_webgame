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

logger = logging.getLogger(__name__)
GUILD_RULES_PATH = Path(__file__).resolve().parent.parent / "data" / "guild_rules.yaml"

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
        "rates": {"silver": 1, "grain": 2, "gold_bar": 50},
        "daily_limits": {"silver": 100000, "grain": 50000, "gold_bar": 20},
        "min_donation_amount": 1,
    },
    "technology": {
        "upgrade_costs": {
            "equipment_forge": {"silver": 5000, "grain": 2000, "gold_bar": 1},
            "experience_refine": {"silver": 5000, "grain": 2000, "gold_bar": 1},
            "resource_supply": {"silver": 4000, "grain": 3000, "gold_bar": 1},
            "military_study": {"silver": 8000, "grain": 3000, "gold_bar": 2},
            "troop_tactics": {"silver": 8000, "grain": 3000, "gold_bar": 2},
            "resource_boost": {"silver": 10000, "grain": 5000, "gold_bar": 3},
            "march_speed": {"silver": 10000, "grain": 5000, "gold_bar": 3},
            "guild_lineup_capacity": {"red_ruby": 1},
            "guild_dispatch_capacity": {"red_ruby": 1},
        },
        "names": {
            "equipment_forge": "装备锻造",
            "experience_refine": "经验炼制",
            "resource_supply": "资源补给",
            "military_study": "兵法研习",
            "troop_tactics": "强兵战术",
            "resource_boost": "资源增产",
            "march_speed": "行军加速",
            "guild_lineup_capacity": "出战位扩容",
            "guild_dispatch_capacity": "出征位扩容",
        },
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
    "hero_pool": {
        "slot_limit": 2,
        "battle_lineup_limit": 20,
        "dispatch_guest_base_limit": 5,
        "replace_cooldown_seconds": 30 * 60,
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


def normalize_guild_rules(raw: Any) -> dict[str, Any]:
    root = ensure_mapping(raw, logger=logger, context="guild rules root") if raw is not None else {}
    config = {
        "pagination": ensure_mapping(root.get("pagination"), logger=logger, context="guild rules.pagination"),
        "creation": ensure_mapping(root.get("creation"), logger=logger, context="guild rules.creation"),
        "contribution": ensure_mapping(root.get("contribution"), logger=logger, context="guild rules.contribution"),
        "technology": ensure_mapping(root.get("technology"), logger=logger, context="guild rules.technology"),
        "warehouse": ensure_mapping(root.get("warehouse"), logger=logger, context="guild rules.warehouse"),
        "hero_pool": ensure_mapping(root.get("hero_pool"), logger=logger, context="guild rules.hero_pool"),
    }

    technology_names = dict(DEFAULT_GUILD_RULES["technology"]["names"])
    technology_names_raw = config["technology"].get("names")
    if isinstance(technology_names_raw, dict):
        for raw_key, raw_value in technology_names_raw.items():
            key = str(raw_key).strip()
            if not key:
                continue
            technology_names[key] = str(raw_value).strip()

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
            "names": technology_names,
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


def refresh_guild_constants() -> None:
    """重新从 YAML 加载帮会规则并更新模块级常量。"""
    global _GUILD_RULES
    global GUILD_LIST_PAGE_SIZE, GUILD_HALL_DISPLAY_LIMIT
    global GUILD_CREATION_COST, GUILD_UPGRADE_BASE_COST
    global CONTRIBUTION_RATES, DAILY_DONATION_LIMITS, MIN_DONATION_AMOUNT
    global TECH_UPGRADE_COSTS, TECH_NAMES
    global EXCHANGE_COSTS, DAILY_EXCHANGE_LIMIT
    global GUILD_HERO_POOL_SLOT_LIMIT, GUILD_BATTLE_LINEUP_LIMIT, GUILD_DISPATCH_GUEST_BASE_LIMIT
    global GUILD_HERO_POOL_REPLACE_COOLDOWN_SECONDS

    _GUILD_RULES = load_guild_rules()

    GUILD_LIST_PAGE_SIZE = _GUILD_RULES["pagination"]["guild_list_page_size"]
    GUILD_HALL_DISPLAY_LIMIT = _GUILD_RULES["pagination"]["guild_hall_display_limit"]

    GUILD_CREATION_COST = _GUILD_RULES["creation"]["guild_creation_cost"]
    GUILD_UPGRADE_BASE_COST = _GUILD_RULES["creation"]["guild_upgrade_base_cost"]

    CONTRIBUTION_RATES = _GUILD_RULES["contribution"]["rates"]
    DAILY_DONATION_LIMITS = _GUILD_RULES["contribution"]["daily_limits"]
    MIN_DONATION_AMOUNT = _GUILD_RULES["contribution"]["min_donation_amount"]

    TECH_UPGRADE_COSTS = _GUILD_RULES["technology"]["upgrade_costs"]
    TECH_NAMES = _GUILD_RULES["technology"]["names"]

    EXCHANGE_COSTS = _GUILD_RULES["warehouse"]["exchange_costs"]
    DAILY_EXCHANGE_LIMIT = _GUILD_RULES["warehouse"]["daily_exchange_limit"]

    GUILD_HERO_POOL_SLOT_LIMIT = _GUILD_RULES["hero_pool"]["slot_limit"]
    GUILD_BATTLE_LINEUP_LIMIT = _GUILD_RULES["hero_pool"]["battle_lineup_limit"]
    GUILD_DISPATCH_GUEST_BASE_LIMIT = _GUILD_RULES["hero_pool"]["dispatch_guest_base_limit"]
    GUILD_HERO_POOL_REPLACE_COOLDOWN_SECONDS = _GUILD_RULES["hero_pool"]["replace_cooldown_seconds"]


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
CONTRIBUTION_RATES = _GUILD_RULES["contribution"]["rates"]
DAILY_DONATION_LIMITS = _GUILD_RULES["contribution"]["daily_limits"]
MIN_DONATION_AMOUNT = _GUILD_RULES["contribution"]["min_donation_amount"]

# ============ 帮会科技 ============
TECH_UPGRADE_COSTS = _GUILD_RULES["technology"]["upgrade_costs"]
TECH_NAMES = _GUILD_RULES["technology"]["names"]

# ============ 帮会仓库 ============
EXCHANGE_COSTS = _GUILD_RULES["warehouse"]["exchange_costs"]
DAILY_EXCHANGE_LIMIT = _GUILD_RULES["warehouse"]["daily_exchange_limit"]

# ============ 帮会门客池 ============
GUILD_HERO_POOL_SLOT_LIMIT = _GUILD_RULES["hero_pool"]["slot_limit"]
GUILD_BATTLE_LINEUP_LIMIT = _GUILD_RULES["hero_pool"]["battle_lineup_limit"]
GUILD_DISPATCH_GUEST_BASE_LIMIT = _GUILD_RULES["hero_pool"]["dispatch_guest_base_limit"]
GUILD_HERO_POOL_REPLACE_COOLDOWN_SECONDS = _GUILD_RULES["hero_pool"]["replace_cooldown_seconds"]
