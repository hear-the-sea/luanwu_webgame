from __future__ import annotations

import copy
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from django.conf import settings

from core.utils.yaml_loader import ensure_mapping, load_yaml_data

logger = logging.getLogger(__name__)

ARENA_RULES_PATH = Path(settings.BASE_DIR) / "data" / "arena_rules.yaml"

DEFAULT_ARENA_RULES: dict[str, Any] = {
    "registration": {
        "max_guests_per_entry": 10,
        "registration_silver_cost": 5000,
        "daily_participation_limit": 5,
        "tournament_player_limit": 10,
    },
    "runtime": {
        "round_interval_seconds": 600,
        "virtual_fill_wait_seconds": 18_000,
        "completed_retention_seconds": 600,
        "round_retry_seconds": 30,
        "recruiting_lock_key": "arena:recruiting_tournament:create",
        "recruiting_lock_timeout": 5,
    },
    "rewards": {
        "base_participation_coins": 30,
        "rank_bonus_coins": {
            1: 280,
            2: 170,
            3: 120,
            4: 90,
            5: 70,
            6: 60,
            7: 50,
            8: 40,
            9: 30,
            10: 20,
        },
    },
}


def _to_positive_int(raw: Any, default: int, *, minimum: int = 1) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return max(minimum, int(default))
    return max(minimum, value)


def _normalize_rank_bonus_coins(raw: Any) -> dict[int, int]:
    default_map = copy.deepcopy(DEFAULT_ARENA_RULES["rewards"]["rank_bonus_coins"])
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


def normalize_arena_rules(raw: Any) -> dict[str, Any]:
    config = copy.deepcopy(DEFAULT_ARENA_RULES)
    root = ensure_mapping(raw, logger=logger, context="arena rules root") if raw is not None else {}

    registration = ensure_mapping(root.get("registration"), logger=logger, context="arena rules.registration")
    runtime = ensure_mapping(root.get("runtime"), logger=logger, context="arena rules.runtime")
    rewards = ensure_mapping(root.get("rewards"), logger=logger, context="arena rules.rewards")

    config["registration"] = {
        "max_guests_per_entry": _to_positive_int(
            registration.get("max_guests_per_entry"),
            config["registration"]["max_guests_per_entry"],
        ),
        "registration_silver_cost": _to_positive_int(
            registration.get("registration_silver_cost"),
            config["registration"]["registration_silver_cost"],
        ),
        "daily_participation_limit": _to_positive_int(
            registration.get("daily_participation_limit"),
            config["registration"]["daily_participation_limit"],
        ),
        "tournament_player_limit": _to_positive_int(
            registration.get("tournament_player_limit"),
            config["registration"]["tournament_player_limit"],
            minimum=2,
        ),
    }
    config["runtime"] = {
        "round_interval_seconds": _to_positive_int(
            runtime.get("round_interval_seconds"),
            config["runtime"]["round_interval_seconds"],
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
        "round_retry_seconds": _to_positive_int(
            runtime.get("round_retry_seconds"),
            config["runtime"]["round_retry_seconds"],
        ),
        "recruiting_lock_key": str(runtime.get("recruiting_lock_key") or config["runtime"]["recruiting_lock_key"]),
        "recruiting_lock_timeout": _to_positive_int(
            runtime.get("recruiting_lock_timeout"),
            config["runtime"]["recruiting_lock_timeout"],
        ),
    }
    config["rewards"] = {
        "base_participation_coins": _to_positive_int(
            rewards.get("base_participation_coins"),
            config["rewards"]["base_participation_coins"],
            minimum=0,
        ),
        "rank_bonus_coins": _normalize_rank_bonus_coins(rewards.get("rank_bonus_coins")),
    }
    return config


@lru_cache(maxsize=1)
def load_arena_rules() -> dict[str, Any]:
    raw = load_yaml_data(
        ARENA_RULES_PATH,
        logger=logger,
        context="arena rules config",
        default=DEFAULT_ARENA_RULES,
    )
    return normalize_arena_rules(raw)


def clear_arena_rules_cache() -> None:
    load_arena_rules.cache_clear()
