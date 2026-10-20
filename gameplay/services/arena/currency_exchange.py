from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db import transaction
from django.db.models import F, Sum

from core.exceptions import ArenaExchangeError, ArenaInsufficientCoinsError, ArenaRewardLimitError
from gameplay.models import ArenaExchangeRecord, Manor
from gameplay.services.inventory.core import add_item_to_inventory_locked

from .helpers import today_bounds

CHUNQIU_COIN_ITEM_KEY = "chunqiu_coin"
ARENA_CURRENCY_EXCHANGE_KEY = "arena_coin_to_chunqiu_coin"
ARENA_CURRENCY_EXCHANGE_NAME = "角斗币兑换春秋币"
ARENA_CURRENCY_EXCHANGE_RATE = 200
ARENA_CURRENCY_EXCHANGE_DAILY_LIMIT = 5


@dataclass(frozen=True, slots=True)
class ArenaCurrencyExchangeResult:
    quantity: int
    arena_coins_spent: int


def _get_exchanged_today(manor: Manor, *, day_start: datetime, day_end: datetime) -> int:
    return int(
        ArenaExchangeRecord.objects.filter(
            manor=manor,
            reward_key=ARENA_CURRENCY_EXCHANGE_KEY,
            created_at__gte=day_start,
            created_at__lt=day_end,
        ).aggregate(total=Sum("quantity"))["total"]
        or 0
    )


def get_arena_currency_exchange_status(manor: Manor) -> dict[str, int | bool]:
    day_start, day_end = today_bounds()
    exchanged_today = _get_exchanged_today(manor, day_start=day_start, day_end=day_end)
    remaining_today = max(0, ARENA_CURRENCY_EXCHANGE_DAILY_LIMIT - exchanged_today)
    arena_coin_balance = max(0, int(manor.arena_coins or 0))
    max_quantity = min(arena_coin_balance // ARENA_CURRENCY_EXCHANGE_RATE, remaining_today)
    return {
        "arena_coin_balance": arena_coin_balance,
        "exchanged_today": exchanged_today,
        "remaining_today": remaining_today,
        "max_quantity": max_quantity,
        "daily_limit": ARENA_CURRENCY_EXCHANGE_DAILY_LIMIT,
        "exchange_rate": ARENA_CURRENCY_EXCHANGE_RATE,
        "can_exchange": max_quantity > 0,
    }


@transaction.atomic
def exchange_arena_coins_for_chunqiu_coins(manor: Manor, quantity: int = 1) -> ArenaCurrencyExchangeResult:
    try:
        normalized_quantity = int(quantity or 0)
    except (TypeError, ValueError) as exc:
        raise ArenaExchangeError("兑换数量无效") from exc
    if normalized_quantity <= 0:
        raise ArenaExchangeError("兑换数量无效")

    locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
    day_start, day_end = today_bounds()
    exchanged_today = _get_exchanged_today(locked_manor, day_start=day_start, day_end=day_end)
    if exchanged_today + normalized_quantity > ARENA_CURRENCY_EXCHANGE_DAILY_LIMIT:
        raise ArenaRewardLimitError(
            ARENA_CURRENCY_EXCHANGE_NAME,
            ARENA_CURRENCY_EXCHANGE_DAILY_LIMIT,
            period="今日",
        )

    arena_coins_spent = normalized_quantity * ARENA_CURRENCY_EXCHANGE_RATE
    if locked_manor.arena_coins < arena_coins_spent:
        raise ArenaInsufficientCoinsError(arena_coins_spent, int(locked_manor.arena_coins))

    locked_manor.arena_coins = F("arena_coins") - arena_coins_spent
    locked_manor.save(update_fields=["arena_coins"])
    add_item_to_inventory_locked(locked_manor, CHUNQIU_COIN_ITEM_KEY, normalized_quantity)
    ArenaExchangeRecord.objects.create(
        manor=locked_manor,
        reward_key=ARENA_CURRENCY_EXCHANGE_KEY,
        reward_name=ARENA_CURRENCY_EXCHANGE_NAME,
        cost_coins=arena_coins_spent,
        quantity=normalized_quantity,
        payload={
            "consumed_resources": {"arena_coins": arena_coins_spent},
            "received_items": {CHUNQIU_COIN_ITEM_KEY: normalized_quantity},
        },
    )
    manor.refresh_from_db(fields=["arena_coins"])
    return ArenaCurrencyExchangeResult(quantity=normalized_quantity, arena_coins_spent=arena_coins_spent)
