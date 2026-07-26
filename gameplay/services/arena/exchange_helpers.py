from __future__ import annotations

import logging
from dataclasses import dataclass

from django.db import transaction
from django.db.models import F, Sum

from core.exceptions import ArenaExchangeError, ArenaInsufficientCoinsError, ArenaRewardLimitError, MessageError
from core.utils.infrastructure import (
    DATABASE_INFRASTRUCTURE_EXCEPTIONS,
    InfrastructureExceptions,
    combine_infrastructure_exceptions,
)
from gameplay.models import ArenaExchangeRecord, Manor, Message
from gameplay.services.buildings.forge import load_blueprint_catalog
from gameplay.services.inventory.core import add_item_to_inventory_locked
from gameplay.services.resources import grant_resources_locked
from gameplay.services.utils.messages import create_message

from . import helpers as _arena_helpers
from .rewards import (
    ArenaRandomItemOption,
    ArenaRewardDefinition,
    get_arena_reward_definition,
    select_weekly_blueprint_key,
)

ARENA_MESSAGE_DELIVERY_EXCEPTIONS: InfrastructureExceptions = combine_infrastructure_exceptions(
    MessageError,
    infrastructure_exceptions=DATABASE_INFRASTRUCTURE_EXCEPTIONS,
)


def normalize_exchange_quantity(quantity: int) -> int:
    try:
        normalized_quantity = int(quantity or 0)
    except (TypeError, ValueError) as exc:
        raise ArenaExchangeError("兑换数量无效") from exc
    if normalized_quantity <= 0:
        raise ArenaExchangeError("兑换数量无效")
    return normalized_quantity


def scale_reward_resources(resources: dict[str, int], quantity: int) -> dict[str, int]:
    return {key: amount * quantity for key, amount in resources.items()}


def scale_reward_items(items: dict[str, int], quantity: int) -> dict[str, int]:
    return {key: amount * quantity for key, amount in items.items()}


def merge_item_grants(*grant_maps: dict[str, int]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for grant_map in grant_maps:
        for item_key, amount in grant_map.items():
            merged[item_key] = merged.get(item_key, 0) + int(amount)
    return merged


def resolve_rotating_blueprint_grants(
    reward: ArenaRewardDefinition,
    quantity: int,
) -> dict[str, int]:
    pool = reward.rotating_blueprint_pool
    if pool is None:
        return {}

    blueprint_key = select_weekly_blueprint_key(pool)
    catalog_entry = load_blueprint_catalog().get(blueprint_key)
    if catalog_entry is None or catalog_entry.rarity != pool.rarity:
        raise ArenaExchangeError("图纸兑换配置无效")
    return {blueprint_key: quantity}


def resolve_random_blueprint_grants(
    reward: ArenaRewardDefinition,
    quantity: int,
) -> dict[str, int]:
    pool = reward.random_blueprint_pool
    if pool is None:
        return {}

    blueprint_keys = sorted(entry.key for entry in load_blueprint_catalog().values() if entry.rarity == pool.rarity)
    if not blueprint_keys:
        raise ArenaExchangeError("图纸兑换配置无效")
    options = tuple(ArenaRandomItemOption(item_key=key, weight=1, amount=1) for key in blueprint_keys)
    return _arena_helpers.resolve_random_reward_items(options, quantity)


def build_exchange_payload(
    *,
    credited_resources: dict[str, int],
    overflow_resources: dict[str, int],
    granted_items: dict[str, int],
) -> dict[str, dict[str, int]]:
    return {
        "resources": credited_resources,
        "resources_overflow": overflow_resources,
        "items": granted_items,
    }


def build_exchange_summary(
    *,
    credited_resources: dict[str, int],
    overflow_resources: dict[str, int],
    granted_items: dict[str, int],
) -> str:
    summary_parts: list[str] = []
    if credited_resources:
        summary_parts.append("资源已发放")
    if granted_items:
        summary_parts.append("道具已入库")
    if overflow_resources:
        summary_parts.append("部分资源因容量上限溢出")
    return "，".join(summary_parts) if summary_parts else "奖励已处理"


def ensure_exchange_limit(
    *,
    arena_exchange_record_model,
    locked_manor,
    reward,
    normalized_quantity: int,
    limit: int | None,
    period: str,
    period_start,
    period_end,
) -> None:
    if limit is None:
        return

    exchanged = (
        arena_exchange_record_model.objects.filter(
            manor=locked_manor,
            reward_key=reward.key,
            created_at__gte=period_start,
            created_at__lt=period_end,
        ).aggregate(total=Sum("quantity"))["total"]
        or 0
    )
    if exchanged + normalized_quantity > limit:
        raise ArenaRewardLimitError(reward.name, limit, period=period)


def grant_exchange_items_locked(
    *,
    fixed_item_grants: dict[str, int],
    random_item_grants: dict[str, int],
    add_item_to_inventory_locked,
    locked_manor,
) -> dict[str, int]:
    for item_key, total_amount in fixed_item_grants.items():
        add_item_to_inventory_locked(locked_manor, item_key, total_amount)
    for item_key, amount in random_item_grants.items():
        add_item_to_inventory_locked(locked_manor, item_key, amount)
    return merge_item_grants(fixed_item_grants, random_item_grants)


def create_exchange_record(
    *,
    arena_exchange_record_model,
    locked_manor,
    reward,
    total_cost: int,
    normalized_quantity: int,
    payload: dict[str, dict[str, int]],
):
    return arena_exchange_record_model.objects.create(
        manor=locked_manor,
        reward_key=reward.key,
        reward_name=reward.name,
        cost_coins=total_cost,
        quantity=normalized_quantity,
        payload=payload,
    )


def send_exchange_success_message(
    *,
    create_message_func,
    message_kind,
    locked_manor,
    reward,
    total_cost: int,
    normalized_quantity: int,
    summary: str,
    logger,
) -> None:
    try:
        create_message_func(
            manor=locked_manor,
            kind=message_kind,
            title=f"竞技场兑换成功：{reward.name}",
            body=f"消耗角斗币 {total_cost}，兑换数量 {normalized_quantity}。{summary}。",
        )
    except ARENA_MESSAGE_DELIVERY_EXCEPTIONS as exc:
        logger.warning(
            "arena exchange message failed: manor_id=%s reward_key=%s quantity=%s error=%s",
            locked_manor.id,
            reward.key,
            normalized_quantity,
            exc,
            exc_info=True,
        )


_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ArenaExchangeResult:
    reward: ArenaRewardDefinition
    quantity: int
    total_cost: int
    credited_resources: dict[str, int]
    overflow_resources: dict[str, int]
    granted_items: dict[str, int]
    random_granted_items: dict[str, int]


@transaction.atomic
def exchange_arena_reward(manor: Manor, reward_key: str, quantity: int = 1) -> ArenaExchangeResult:
    reward = get_arena_reward_definition(reward_key)
    if not reward:
        raise ArenaExchangeError("兑换项不存在")

    normalized_quantity = normalize_exchange_quantity(quantity)

    locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
    total_cost = reward.cost_coins * normalized_quantity
    if locked_manor.arena_coins < total_cost:
        raise ArenaInsufficientCoinsError(total_cost, int(locked_manor.arena_coins))

    day_start, day_end = _arena_helpers.today_bounds()
    ensure_exchange_limit(
        arena_exchange_record_model=ArenaExchangeRecord,
        locked_manor=locked_manor,
        reward=reward,
        normalized_quantity=normalized_quantity,
        limit=reward.daily_limit,
        period="今日",
        period_start=day_start,
        period_end=day_end,
    )
    week_start, week_end = _arena_helpers.current_week_bounds()
    ensure_exchange_limit(
        arena_exchange_record_model=ArenaExchangeRecord,
        locked_manor=locked_manor,
        reward=reward,
        normalized_quantity=normalized_quantity,
        limit=reward.weekly_limit,
        period="本周",
        period_start=week_start,
        period_end=week_end,
    )

    rotating_blueprint_grants = resolve_rotating_blueprint_grants(reward, normalized_quantity)
    random_blueprint_grants = resolve_random_blueprint_grants(reward, normalized_quantity)

    locked_manor.arena_coins = F("arena_coins") - total_cost
    locked_manor.save(update_fields=["arena_coins"])

    reward_resources = scale_reward_resources(reward.resources, normalized_quantity)
    credited_resources, overflow_resources = grant_resources_locked(
        locked_manor,
        reward_resources,
        note=f"竞技场兑换：{reward.name}",
        sync_production=False,
    )

    fixed_item_grants = merge_item_grants(
        scale_reward_items(reward.items, normalized_quantity),
        rotating_blueprint_grants,
    )
    random_item_grants = merge_item_grants(
        _arena_helpers.resolve_random_reward_items(reward.random_items, normalized_quantity),
        random_blueprint_grants,
    )
    granted_items = grant_exchange_items_locked(
        fixed_item_grants=fixed_item_grants,
        random_item_grants=random_item_grants,
        add_item_to_inventory_locked=add_item_to_inventory_locked,
        locked_manor=locked_manor,
    )

    payload = build_exchange_payload(
        credited_resources=credited_resources,
        overflow_resources=overflow_resources,
        granted_items=granted_items,
    )
    create_exchange_record(
        arena_exchange_record_model=ArenaExchangeRecord,
        locked_manor=locked_manor,
        reward=reward,
        total_cost=total_cost,
        normalized_quantity=normalized_quantity,
        payload=payload,
    )

    summary = build_exchange_summary(
        credited_resources=credited_resources,
        overflow_resources=overflow_resources,
        granted_items=granted_items,
    )

    send_exchange_success_message(
        create_message_func=create_message,
        message_kind=Message.Kind.REWARD,
        locked_manor=locked_manor,
        reward=reward,
        total_cost=total_cost,
        normalized_quantity=normalized_quantity,
        summary=summary,
        logger=_logger,
    )

    manor.refresh_from_db(fields=["arena_coins", "grain", "silver"])
    return ArenaExchangeResult(
        reward=reward,
        quantity=normalized_quantity,
        total_cost=total_cost,
        credited_resources=credited_resources,
        overflow_resources=overflow_resources,
        granted_items=granted_items,
        random_granted_items=random_item_grants,
    )
