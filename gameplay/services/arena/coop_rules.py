from __future__ import annotations

import copy
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from django.conf import settings

from core.utils.yaml_loader import ensure_mapping, load_yaml_data

logger = logging.getLogger(__name__)

ARENA_COOP_RULES_PATH = Path(settings.BASE_DIR) / "data" / "arena_coop_rules.yaml"

DEFAULT_ARENA_COOP_RULES: dict[str, Any] = {
    "registration": {
        "player_limit": 5,
        "guest_limit_per_entry": 3,
        "daily_participation_limit": 2,
        "prepare_duration_seconds": 120,
        "registration_silver_cost": 0,
        "recruiting_lock_key": "arena:coop:recruiting_event:create",
        "recruiting_lock_timeout": 5,
    },
    "runtime": {
        "auto_start_scan_seconds": 30,
        "virtual_fill_wait_seconds": 28_800,
        "completed_retention_seconds": 86400,
    },
    "contribution": {
        "minimum_share_bps": 500,
    },
    "rewards": {
        "participation_coins": 30,
        "clear_coins": 40,
        "damage_tiers": [
            {"min_share_bps": 1000, "coins": 20},
            {"min_share_bps": 1500, "coins": 40},
            {"min_share_bps": 2000, "coins": 70},
            {"min_share_bps": 2500, "coins": 100},
        ],
        "rank_rewards": {1: 80, 2: 50, 3: 30},
    },
    "rare_drop": {
        "enabled": True,
        "item_key": "equip_tulongdao",
        "item_choices": [],
        "chance_bps": 10,
        "requires_clear": True,
        "requires_minimum_contribution": True,
    },
    "enemy": {
        "boss": {"template_key": "arena_gl_top_zhang_wuji_boss", "display_name": "张无忌"},
        "guards": [
            {"template_key": "arena_gl_top_yang_xiao_guard", "display_name": "杨逍"},
            {"template_key": "arena_gl_top_wei_yixiao_guard", "display_name": "韦一笑"},
            {"template_key": "arena_gl_top_five_flags_elite_front", "display_name": "五行旗精锐"},
            {"template_key": "arena_gl_top_five_flags_elite_rear", "display_name": "五行旗精锐"},
        ],
    },
}


def _to_positive_int(raw: Any, default: int, *, minimum: int = 0) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return max(minimum, int(default))
    return max(minimum, value)


def _normalize_rank_rewards(raw: Any) -> dict[int, int]:
    default_map = copy.deepcopy(DEFAULT_ARENA_COOP_RULES["rewards"]["rank_rewards"])
    if not isinstance(raw, dict):
        return default_map

    result: dict[int, int] = {}
    for raw_rank, raw_bonus in raw.items():
        try:
            rank = int(raw_rank)
        except (TypeError, ValueError):
            continue
        if rank <= 0:
            continue
        result[rank] = max(0, int(raw_bonus or 0))
    return result or default_map


def _normalize_damage_tiers(raw: Any) -> list[dict[str, int]]:
    default_rows = copy.deepcopy(DEFAULT_ARENA_COOP_RULES["rewards"]["damage_tiers"])
    if not isinstance(raw, list):
        return default_rows

    rows: list[dict[str, int]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        min_share_bps = _to_positive_int(entry.get("min_share_bps"), 0, minimum=0)
        coins = _to_positive_int(entry.get("coins"), 0, minimum=0)
        rows.append({"min_share_bps": min_share_bps, "coins": coins})
    return rows or default_rows


def _normalize_rare_drop_choices(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    choices: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        item_key = str(entry.get("item_key") or entry.get("key") or "").strip()
        weight = _to_positive_int(entry.get("weight"), 0, minimum=0)
        if item_key and weight > 0:
            choices.append({"item_key": item_key, "weight": weight})
    return choices


def normalize_arena_coop_rules(raw: Any) -> dict[str, Any]:
    config = copy.deepcopy(DEFAULT_ARENA_COOP_RULES)
    root = ensure_mapping(raw, logger=logger, context="arena coop rules root") if raw is not None else {}

    registration = ensure_mapping(root.get("registration"), logger=logger, context="arena coop rules.registration")
    runtime = ensure_mapping(root.get("runtime"), logger=logger, context="arena coop rules.runtime")
    contribution = ensure_mapping(
        root.get("contribution"),
        logger=logger,
        context="arena coop rules.contribution",
    )
    rewards = ensure_mapping(root.get("rewards"), logger=logger, context="arena coop rules.rewards")
    rare_drop = ensure_mapping(root.get("rare_drop"), logger=logger, context="arena coop rules.rare_drop")
    enemy = ensure_mapping(root.get("enemy"), logger=logger, context="arena coop rules.enemy")
    boss = ensure_mapping(enemy.get("boss"), logger=logger, context="arena coop rules.enemy.boss")
    guards_raw = enemy.get("guards")

    guards: list[dict[str, str]] = []
    if isinstance(guards_raw, list):
        for row in guards_raw:
            if not isinstance(row, dict):
                continue
            template_key = str(row.get("template_key") or "").strip()
            display_name = str(row.get("display_name") or "").strip()
            if template_key:
                guards.append({"template_key": template_key, "display_name": display_name or template_key})
    if not guards:
        guards = copy.deepcopy(DEFAULT_ARENA_COOP_RULES["enemy"]["guards"])

    config["registration"] = {
        "player_limit": _to_positive_int(
            registration.get("player_limit"),
            config["registration"]["player_limit"],
            minimum=2,
        ),
        "guest_limit_per_entry": _to_positive_int(
            registration.get("guest_limit_per_entry"),
            config["registration"]["guest_limit_per_entry"],
            minimum=1,
        ),
        "daily_participation_limit": _to_positive_int(
            registration.get("daily_participation_limit"),
            config["registration"]["daily_participation_limit"],
            minimum=1,
        ),
        "prepare_duration_seconds": _to_positive_int(
            registration.get("prepare_duration_seconds"),
            config["registration"]["prepare_duration_seconds"],
            minimum=1,
        ),
        "registration_silver_cost": _to_positive_int(
            registration.get("registration_silver_cost"),
            config["registration"]["registration_silver_cost"],
            minimum=0,
        ),
        "recruiting_lock_key": str(
            registration.get("recruiting_lock_key") or config["registration"]["recruiting_lock_key"]
        ),
        "recruiting_lock_timeout": _to_positive_int(
            registration.get("recruiting_lock_timeout"),
            config["registration"]["recruiting_lock_timeout"],
            minimum=1,
        ),
    }
    config["runtime"] = {
        "auto_start_scan_seconds": _to_positive_int(
            runtime.get("auto_start_scan_seconds"),
            config["runtime"]["auto_start_scan_seconds"],
            minimum=1,
        ),
        "virtual_fill_wait_seconds": _to_positive_int(
            runtime.get("virtual_fill_wait_seconds"),
            config["runtime"]["virtual_fill_wait_seconds"],
        ),
        "completed_retention_seconds": _to_positive_int(
            runtime.get("completed_retention_seconds"),
            config["runtime"]["completed_retention_seconds"],
            minimum=0,
        ),
    }
    config["contribution"] = {
        "minimum_share_bps": _to_positive_int(
            contribution.get("minimum_share_bps"),
            config["contribution"]["minimum_share_bps"],
            minimum=0,
        ),
    }
    config["rewards"] = {
        "participation_coins": _to_positive_int(
            rewards.get("participation_coins"),
            config["rewards"]["participation_coins"],
            minimum=0,
        ),
        "clear_coins": _to_positive_int(
            rewards.get("clear_coins"),
            config["rewards"]["clear_coins"],
            minimum=0,
        ),
        "damage_tiers": _normalize_damage_tiers(rewards.get("damage_tiers")),
        "rank_rewards": _normalize_rank_rewards(rewards.get("rank_rewards")),
    }
    config["rare_drop"] = {
        "enabled": bool(rare_drop.get("enabled", config["rare_drop"]["enabled"])),
        "item_key": str(rare_drop.get("item_key") or config["rare_drop"]["item_key"]),
        "item_choices": _normalize_rare_drop_choices(rare_drop.get("item_choices")),
        "chance_bps": _to_positive_int(
            rare_drop.get("chance_bps"),
            config["rare_drop"]["chance_bps"],
            minimum=0,
        ),
        "requires_clear": bool(rare_drop.get("requires_clear", config["rare_drop"]["requires_clear"])),
        "requires_minimum_contribution": bool(
            rare_drop.get(
                "requires_minimum_contribution",
                config["rare_drop"]["requires_minimum_contribution"],
            )
        ),
    }
    config["enemy"] = {
        "boss": {
            "template_key": str(boss.get("template_key") or config["enemy"]["boss"]["template_key"]),
            "display_name": str(boss.get("display_name") or config["enemy"]["boss"]["display_name"]),
        },
        "guards": guards,
    }
    return config


@lru_cache(maxsize=1)
def load_arena_coop_rules() -> dict[str, Any]:
    raw = load_yaml_data(
        ARENA_COOP_RULES_PATH,
        logger=logger,
        context="arena coop rules config",
        default=DEFAULT_ARENA_COOP_RULES,
    )
    return normalize_arena_coop_rules(raw)


def clear_arena_coop_rules_cache() -> None:
    load_arena_coop_rules.cache_clear()
