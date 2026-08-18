"""
护院钱庄服务
"""

from __future__ import annotations

from typing import Any

from django.db import DatabaseError, transaction
from django.db.models import Sum

from core.exceptions import TradeValidationError, TroopCapacityFullError

from ...models import Manor, PlayerTroop, TroopBankStorage
from .troop_capacity import (
    MANOR_TROOP_CAPACITY,
    TROOP_BANK_CAPACITY,
    ensure_manor_troop_capacity_locked,
    get_manor_troop_remaining_space,
    get_manor_troop_used_space,
)


def _normalize_positive_quantity(quantity: int, *, action_label: str) -> int:
    try:
        normalized = int(quantity)
    except (TypeError, ValueError) as exc:
        raise TradeValidationError(f"{action_label}数量必须是正整数") from exc
    if normalized <= 0:
        raise TradeValidationError(f"{action_label}数量必须大于0")
    return normalized


def get_troop_bank_capacity(_manor: Manor | None = None) -> int:
    return TROOP_BANK_CAPACITY


def get_troop_bank_used_space(manor: Manor) -> int:
    try:
        used = TroopBankStorage.objects.filter(manor=manor).aggregate(total=Sum("count")).get("total") or 0
        return max(0, int(used))
    except DatabaseError:
        # 兼容迁移尚未完成/表异常场景：不阻断页面渲染。
        return 0


def get_troop_bank_remaining_space(manor: Manor) -> int:
    return max(0, TROOP_BANK_CAPACITY - get_troop_bank_used_space(manor))


def get_troop_bank_rows(manor: Manor) -> list[dict[str, Any]]:
    rows_by_key: dict[str, dict[str, Any]] = {}

    try:
        player_troops = list(
            PlayerTroop.objects.filter(manor=manor, count__gt=0)
            .select_related("troop_template")
            .order_by("troop_template__priority")
        )
    except DatabaseError:
        player_troops = []

    try:
        bank_troops = list(
            TroopBankStorage.objects.filter(manor=manor, count__gt=0)
            .select_related("troop_template")
            .order_by("troop_template__priority")
        )
    except DatabaseError:
        # 钱庄表异常时，仍展示庄园已有护院，避免误导为“暂无可存取护院”。
        bank_troops = []

    for player_troop in player_troops:
        template = player_troop.troop_template
        rows_by_key[template.key] = {
            "key": template.key,
            "name": template.name,
            "avatar": template.avatar.url if template.avatar else "",
            "priority": template.priority,
            "player_count": player_troop.count,
            "bank_count": 0,
        }

    for bank_troop in bank_troops:
        template = bank_troop.troop_template
        row = rows_by_key.setdefault(
            template.key,
            {
                "key": template.key,
                "name": template.name,
                "avatar": template.avatar.url if template.avatar else "",
                "priority": template.priority,
                "player_count": 0,
                "bank_count": 0,
            },
        )
        row["bank_count"] = bank_troop.count

    rows = list(rows_by_key.values())
    rows.sort(key=lambda row: (row.get("priority", 0), row.get("key", "")))
    return rows


def _lock_manor(manor: Manor) -> Manor:
    return Manor.objects.select_for_update().get(pk=manor.pk)


@transaction.atomic
def deposit_troops_to_bank(manor: Manor, troop_key: str, quantity: int) -> dict[str, Any]:
    quantity = _normalize_positive_quantity(quantity, action_label="存入")

    locked_manor = _lock_manor(manor)

    player_troop = (
        PlayerTroop.objects.select_for_update()
        .filter(manor=locked_manor, troop_template__key=troop_key)
        .select_related("troop_template")
        .first()
    )
    if not player_troop:
        raise TradeValidationError("没有该类型的护院")
    if player_troop.count < quantity:
        raise TradeValidationError(f"{player_troop.troop_template.name}数量不足，当前可用{player_troop.count}")

    used = get_troop_bank_used_space(locked_manor)
    if used + quantity > TROOP_BANK_CAPACITY:
        raise TradeValidationError(f"钱庄护院容量不足，最多可存放{TROOP_BANK_CAPACITY}名")

    bank_troop, _created = TroopBankStorage.objects.get_or_create(
        manor=locked_manor,
        troop_template=player_troop.troop_template,
        defaults={"count": 0},
    )
    bank_troop.count += quantity
    bank_troop.save(update_fields=["count", "updated_at"])

    player_troop.count -= quantity
    player_troop.save(update_fields=["count", "updated_at"])

    return {
        "troop_name": player_troop.troop_template.name,
        "quantity": quantity,
        "used": used + quantity,
        "capacity": TROOP_BANK_CAPACITY,
    }


@transaction.atomic
def withdraw_troops_from_bank(manor: Manor, troop_key: str, quantity: int) -> dict[str, Any]:
    quantity = _normalize_positive_quantity(quantity, action_label="取出")

    locked_manor = _lock_manor(manor)

    bank_troop = (
        TroopBankStorage.objects.select_for_update()
        .filter(manor=locked_manor, troop_template__key=troop_key)
        .select_related("troop_template")
        .first()
    )
    if not bank_troop:
        raise TradeValidationError("钱庄中没有该类型护院")
    if bank_troop.count < quantity:
        raise TradeValidationError(f"钱庄中{bank_troop.troop_template.name}数量不足，当前仅有{bank_troop.count}")

    try:
        ensure_manor_troop_capacity_locked(locked_manor, quantity)
    except TroopCapacityFullError as exc:
        raise TradeValidationError(
            f"庄园内护院容量不足，当前最多还可容纳{get_manor_troop_remaining_space(locked_manor)}名"
        ) from exc

    player_troop, _created = PlayerTroop.objects.get_or_create(
        manor=locked_manor,
        troop_template=bank_troop.troop_template,
        defaults={"count": 0},
    )
    player_troop.count += quantity
    player_troop.save(update_fields=["count", "updated_at"])

    bank_troop.count -= quantity
    if bank_troop.count <= 0:
        bank_troop.delete()
    else:
        bank_troop.save(update_fields=["count", "updated_at"])

    return {
        "troop_name": player_troop.troop_template.name,
        "quantity": quantity,
        "used": get_troop_bank_used_space(locked_manor),
        "capacity": TROOP_BANK_CAPACITY,
        "manor_used": get_manor_troop_used_space(locked_manor),
        "manor_capacity": MANOR_TROOP_CAPACITY,
    }
