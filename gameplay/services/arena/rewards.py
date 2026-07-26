from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from django.conf import settings
from django.utils import timezone

from common.constants.resources import ResourceType
from core.utils.yaml_loader import ensure_list, ensure_mapping, load_yaml_data

logger = logging.getLogger(__name__)

ARENA_REWARDS_PATH = Path(settings.BASE_DIR) / "data" / "arena_rewards.yaml"


@dataclass(frozen=True)
class ArenaRandomItemOption:
    item_key: str
    weight: int
    amount: int


@dataclass(frozen=True)
class ArenaRotatingBlueprintPool:
    rarity: str
    blueprint_keys: tuple[str, ...]


@dataclass(frozen=True)
class ArenaRandomBlueprintPool:
    rarity: str


@dataclass(frozen=True)
class ArenaRewardDefinition:
    key: str
    name: str
    cost_coins: int
    daily_limit: int | None
    weekly_limit: int | None
    resources: dict[str, int]
    items: dict[str, int]
    random_items: tuple[ArenaRandomItemOption, ...]
    description: str = ""
    rotating_blueprint_pool: ArenaRotatingBlueprintPool | None = None
    random_blueprint_pool: ArenaRandomBlueprintPool | None = None


def _to_positive_int(raw: Any, *, default: int = 0) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return max(0, int(default))
    return max(0, value)


def _normalize_random_items(rewards: dict[str, Any], *, context: str) -> tuple[ArenaRandomItemOption, ...]:
    rows = ensure_list(rewards.get("random_items"), logger=logger, context=f"{context}.rewards.random_items")
    options: list[ArenaRandomItemOption] = []
    for idx, row in enumerate(rows):
        row_context = f"{context}.rewards.random_items[{idx}]"
        entry = ensure_mapping(row, logger=logger, context=row_context)
        item_key = str(entry.get("item_key") or "").strip()
        if not item_key:
            logger.warning("arena reward random item without item_key: %s", row_context)
            continue
        weight = _to_positive_int(entry.get("weight"))
        if weight <= 0:
            logger.warning("arena reward random item with invalid weight: %s", row_context)
            continue
        amount = _to_positive_int(entry.get("amount"), default=1)
        if amount <= 0:
            logger.warning("arena reward random item with invalid amount: %s", row_context)
            continue
        options.append(ArenaRandomItemOption(item_key=item_key, weight=weight, amount=amount))
    return tuple(options)


def _normalize_rotating_blueprint_pool(payload: dict[str, Any], *, context: str) -> ArenaRotatingBlueprintPool | None:
    raw_pool = payload.get("rotating_blueprint_pool")
    if raw_pool is None:
        return None

    pool = ensure_mapping(raw_pool, logger=logger, context=f"{context}.rotating_blueprint_pool")
    rarity = str(pool.get("rarity") or "").strip()
    raw_keys = ensure_list(
        pool.get("blueprint_keys"),
        logger=logger,
        context=f"{context}.rotating_blueprint_pool.blueprint_keys",
    )
    blueprint_keys = tuple(str(key).strip() for key in raw_keys if str(key).strip())
    if not rarity or len(blueprint_keys) != 4 or len(set(blueprint_keys)) != len(blueprint_keys):
        logger.warning("skip invalid rotating blueprint pool: %s", context)
        return None
    return ArenaRotatingBlueprintPool(rarity=rarity, blueprint_keys=blueprint_keys)


def _normalize_random_blueprint_pool(payload: dict[str, Any], *, context: str) -> ArenaRandomBlueprintPool | None:
    raw_pool = payload.get("random_blueprint_pool")
    if raw_pool is None:
        return None

    pool = ensure_mapping(raw_pool, logger=logger, context=f"{context}.random_blueprint_pool")
    rarity = str(pool.get("rarity") or "").strip()
    if not rarity:
        logger.warning("skip invalid random blueprint pool: %s", context)
        return None
    return ArenaRandomBlueprintPool(rarity=rarity)


def select_weekly_blueprint_key(pool: ArenaRotatingBlueprintPool, *, today=None) -> str:
    """Return this ISO week's deterministic blueprint from a four-item pool."""
    iso_week = (today or timezone.localdate()).isocalendar().week
    return pool.blueprint_keys[(iso_week - 1) % len(pool.blueprint_keys)]


def _normalize_reward_payload(
    payload: dict[str, Any], *, context: str
) -> tuple[dict[str, int], dict[str, int], tuple[ArenaRandomItemOption, ...]]:
    rewards = ensure_mapping(payload.get("rewards"), logger=logger, context=f"{context}.rewards")
    resources_raw = ensure_mapping(rewards.get("resources"), logger=logger, context=f"{context}.rewards.resources")
    items_raw = ensure_mapping(rewards.get("items"), logger=logger, context=f"{context}.rewards.items")

    resources: dict[str, int] = {}
    for key, amount in resources_raw.items():
        normalized_key = str(key).strip()
        if normalized_key not in ResourceType.values:
            logger.warning("arena reward contains unknown resource key: %s (%s)", normalized_key, context)
            continue
        normalized_amount = _to_positive_int(amount)
        if normalized_amount > 0:
            resources[normalized_key] = normalized_amount

    items: dict[str, int] = {}
    for key, amount in items_raw.items():
        normalized_key = str(key).strip()
        if not normalized_key:
            continue
        normalized_amount = _to_positive_int(amount)
        if normalized_amount > 0:
            items[normalized_key] = normalized_amount

    random_items = _normalize_random_items(rewards, context=context)
    return resources, items, random_items


@lru_cache(maxsize=1)
def load_arena_reward_catalog() -> dict[str, ArenaRewardDefinition]:
    raw = load_yaml_data(
        ARENA_REWARDS_PATH,
        logger=logger,
        context="arena rewards config",
        default={"rewards": []},
    )
    payload = ensure_mapping(raw, logger=logger, context="arena rewards root")
    rows = ensure_list(payload.get("rewards"), logger=logger, context="arena rewards entries")

    catalog: dict[str, ArenaRewardDefinition] = {}
    for idx, row in enumerate(rows):
        context = f"arena rewards[{idx}]"
        entry = ensure_mapping(row, logger=logger, context=context)
        key = str(entry.get("key") or "").strip()
        if not key:
            logger.warning("skip arena reward without key: %s", context)
            continue
        if key in catalog:
            logger.warning("duplicate arena reward key ignored: %s", key)
            continue

        cost_coins = _to_positive_int(entry.get("cost_coins"))
        if cost_coins <= 0:
            logger.warning("skip arena reward with invalid cost_coins: %s", key)
            continue

        resources, items, random_items = _normalize_reward_payload(entry, context=context)
        rotating_blueprint_pool = _normalize_rotating_blueprint_pool(entry, context=context)
        random_blueprint_pool = _normalize_random_blueprint_pool(entry, context=context)
        if rotating_blueprint_pool is not None and random_blueprint_pool is not None:
            logger.warning("skip arena reward with conflicting blueprint pools: %s", key)
            continue
        if (
            not resources
            and not items
            and not random_items
            and rotating_blueprint_pool is None
            and random_blueprint_pool is None
        ):
            logger.warning("skip arena reward without payload: %s", key)
            continue

        daily_limit_value = _to_positive_int(entry.get("daily_limit"))
        daily_limit = daily_limit_value if daily_limit_value > 0 else None
        weekly_limit_value = _to_positive_int(entry.get("weekly_limit"))
        weekly_limit = weekly_limit_value if weekly_limit_value > 0 else None

        catalog[key] = ArenaRewardDefinition(
            key=key,
            name=str(entry.get("name") or "竞技奖励"),
            cost_coins=cost_coins,
            daily_limit=daily_limit,
            weekly_limit=weekly_limit,
            resources=resources,
            items=items,
            random_items=random_items,
            description=str(entry.get("description") or ""),
            rotating_blueprint_pool=rotating_blueprint_pool,
            random_blueprint_pool=random_blueprint_pool,
        )

    return catalog


def get_arena_reward_definition(reward_key: str) -> ArenaRewardDefinition | None:
    key = str(reward_key or "").strip()
    if not key:
        return None
    return load_arena_reward_catalog().get(key)


def clear_arena_reward_cache() -> None:
    load_arena_reward_catalog.cache_clear()
