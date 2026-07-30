from __future__ import annotations

import logging
from typing import Any

from django.db import IntegrityError
from django.utils import timezone

from gameplay.models import BotInventoryDailyCounter, ItemTemplate

logger = logging.getLogger(__name__)

RARE_ITEM_RARITIES = {"purple", "orange", "red", "legendary"}


def inventory_daily_cap_limits(
    template: ItemTemplate,
    *,
    config: dict[str, Any],
) -> tuple[tuple[str, int], ...]:
    projection = config.get("projection") or {}
    checks: list[tuple[str, int]] = []
    if str(template.rarity or "").lower() in RARE_ITEM_RARITIES:
        checks.append(("rare", int(projection.get("rare_item_daily_global_cap") or 0)))
    powerful_min_price = int(projection.get("powerful_item_min_price") or 100_000)
    if int(template.price or 0) >= powerful_min_price:
        checks.append(
            (
                "powerful",
                int(projection.get("powerful_item_daily_global_cap") or 0),
            )
        )
    return tuple(sorted(checks))


def reserve_inventory_daily_cap(*, category: str, requested: int, cap: int, now) -> int:
    requested = max(0, int(requested or 0))
    cap = max(0, int(cap or 0))
    if requested <= 0 or cap <= 0:
        return requested

    counter_date = timezone.localtime(now).date()
    counter = lock_inventory_daily_counter(category=str(category), counter_date=counter_date)
    allowed = min(requested, max(0, cap - int(counter.quantity or 0)))
    if allowed > 0:
        counter.quantity = int(counter.quantity or 0) + allowed
        counter.save(update_fields=["quantity", "updated_at"])
    if allowed < requested:
        logger.info(
            "Virtual player inventory cap truncated: category=%s requested=%s allowed=%s cap=%s date=%s",
            category,
            requested,
            allowed,
            cap,
            counter_date.isoformat(),
            extra={
                "event": "virtual_player_inventory_cap_truncated",
                "category": str(category),
                "requested": requested,
                "allowed": allowed,
                "cap": cap,
                "date": counter_date.isoformat(),
            },
        )
    return allowed


def lock_inventory_daily_counter(*, category: str, counter_date):
    locked = BotInventoryDailyCounter.objects.select_for_update()
    try:
        counter, _created = locked.get_or_create(
            category=category,
            counter_date=counter_date,
            defaults={"quantity": 0},
        )
    except IntegrityError:
        counter = BotInventoryDailyCounter.objects.select_for_update().get(
            category=category,
            counter_date=counter_date,
        )
    return counter


def release_inventory_daily_cap(*, category: str, amount: int, now) -> None:
    amount = max(0, int(amount or 0))
    if amount <= 0:
        return
    counter_date = timezone.localtime(now).date()
    counter = (
        BotInventoryDailyCounter.objects.select_for_update()
        .filter(category=str(category), counter_date=counter_date)
        .first()
    )
    if counter is None:
        return
    counter.quantity = max(0, int(counter.quantity or 0) - amount)
    counter.save(update_fields=["quantity", "updated_at"])


def apply_inventory_daily_caps(
    template: ItemTemplate,
    *,
    quantity: int,
    config: dict[str, Any],
    now,
) -> int:
    quantity = max(0, int(quantity or 0))
    reservations: list[tuple[str, int]] = []
    for category, cap in inventory_daily_cap_limits(template, config=config):
        previous_quantity = quantity
        quantity = reserve_inventory_daily_cap(category=category, requested=quantity, cap=cap, now=now)
        if previous_quantity > quantity:
            for reserved_category, reserved_amount in reservations:
                release_inventory_daily_cap(
                    category=reserved_category,
                    amount=min(reserved_amount, previous_quantity - quantity),
                    now=now,
                )
        if quantity <= 0:
            return 0
        reservations.append((category, quantity))
    return quantity


__all__ = [
    "apply_inventory_daily_caps",
    "inventory_daily_cap_limits",
    "lock_inventory_daily_counter",
    "release_inventory_daily_cap",
    "reserve_inventory_daily_cap",
]
