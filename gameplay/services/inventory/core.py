"""
Inventory row operations: add / consume / query.

These functions are intentionally kept free of "item use" business logic, which
lives in `gameplay.services.inventory.use`.
"""

from __future__ import annotations

from typing import Any, Callable, Dict

from django.db import IntegrityError, transaction
from django.db.models import F, Sum
from django.db.models.functions import Now

from core.exceptions import InsufficientStockError, ItemNotFoundError
from gameplay.models import InventoryItem, ItemTemplate, Manor

# 粮食物品模板 key
GRAIN_ITEM_KEY = "grain"


def _require_atomic_block(name: str) -> None:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError(f"{name} must be called inside transaction.atomic()")


def add_item_to_inventory_locked(
    manor: Manor,
    item_key: str,
    quantity: int = 1,
    storage_location: str = InventoryItem.StorageLocation.WAREHOUSE,
) -> InventoryItem:
    """
    向庄园背包添加物品（假设调用方已在 transaction.atomic 中完成所需的并发控制）。

    该函数不会创建新的事务块；适用于上层服务函数已处于事务中并希望避免嵌套事务的冗余开销。
    """
    _require_atomic_block("add_item_to_inventory_locked")
    template = ItemTemplate.objects.filter(key=item_key).first()
    if not template:
        raise ItemNotFoundError("物品不存在", item_key=item_key)

    if quantity <= 0:
        raise AssertionError("add_item_to_inventory_locked requires positive quantity")

    # Atomic increment to avoid lost updates under concurrent requests.
    updated = InventoryItem.objects.filter(
        manor=manor,
        template=template,
        storage_location=storage_location,
    ).update(quantity=F("quantity") + int(quantity), updated_at=Now())
    if updated == 0:
        try:
            InventoryItem.objects.create(
                manor=manor,
                template=template,
                storage_location=storage_location,
                quantity=int(quantity),
            )
        except IntegrityError:
            # Another request created the row concurrently; retry atomic increment.
            InventoryItem.objects.filter(
                manor=manor,
                template=template,
                storage_location=storage_location,
            ).update(quantity=F("quantity") + int(quantity), updated_at=Now())

    item = (
        InventoryItem.objects.select_related("template")
        .filter(manor=manor, template=template, storage_location=storage_location)
        .first()
    )
    if not item:
        raise RuntimeError("failed to create or update inventory item")

    # 粮食存入仓库时，同步更新 Manor.grain
    if item_key == GRAIN_ITEM_KEY and storage_location == InventoryItem.StorageLocation.WAREHOUSE:
        Manor.objects.filter(pk=manor.pk).update(grain=F("grain") + quantity)
        manor.grain = getattr(manor, "grain", 0) + quantity

    return item


def consume_inventory_item_locked(
    locked_item: InventoryItem,
    amount: int = 1,
    *,
    allow_frozen_gold_bars: bool = False,
) -> None:
    """
    消耗背包物品（假设传入的 item 行已在当前事务中被锁定）。
    """
    _require_atomic_block("consume_inventory_item_locked")
    consume_amount = int(amount or 1)
    if consume_amount <= 0:
        return
    if not locked_item.pk:
        raise ItemNotFoundError()

    item_name = getattr(getattr(locked_item, "template", None), "name", "物品")
    if locked_item.quantity < consume_amount:
        raise InsufficientStockError(item_name, consume_amount, locked_item.quantity)
    if (
        locked_item.template.key == "gold_bar"
        and locked_item.storage_location == InventoryItem.StorageLocation.WAREHOUSE
        and not allow_frozen_gold_bars
    ):
        frozen = _get_frozen_gold_bar_quantity(locked_item.manor)
        available = max(0, int(locked_item.quantity or 0) - frozen)
        if available < consume_amount:
            raise InsufficientStockError(item_name, consume_amount, available)

    new_qty = int(locked_item.quantity) - int(consume_amount)

    # 粮食从仓库消耗时，同步更新 Manor.grain
    if (
        locked_item.template.key == GRAIN_ITEM_KEY
        and locked_item.storage_location == InventoryItem.StorageLocation.WAREHOUSE
    ):
        Manor.objects.filter(pk=locked_item.manor_id).update(grain=F("grain") - int(consume_amount))

    if new_qty <= 0:
        locked_item.delete()
    else:
        InventoryItem.objects.filter(pk=locked_item.pk).update(quantity=new_qty, updated_at=Now())
        locked_item.quantity = new_qty


def _get_frozen_gold_bar_quantity(manor: Manor) -> int:
    from trade.models import FrozenGoldBar

    result = FrozenGoldBar.objects.filter(manor=manor, is_frozen=True).aggregate(total=Sum("amount"))
    return int(result["total"] or 0)


def consume_inventory_item_for_manor_locked(
    manor: Manor,
    item_key: str,
    amount: int = 1,
    *,
    allow_frozen_gold_bars: bool = False,
) -> None:
    """
    按物品 key 消耗庄园仓库物品（在事务内加锁该库存行，但不创建新的事务块）。
    """
    _require_atomic_block("consume_inventory_item_for_manor_locked")
    consume_amount = int(amount or 1)
    if consume_amount <= 0:
        return
    locked = (
        InventoryItem.objects.select_for_update()
        .select_related("template", "manor")
        .filter(
            manor=manor,
            template__key=str(item_key),
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )
        .first()
    )
    if not locked:
        template = ItemTemplate.objects.filter(key=str(item_key)).only("name").first()
        raise InsufficientStockError(template.name if template else str(item_key), consume_amount, 0)
    if str(item_key) == "gold_bar" and not allow_frozen_gold_bars:
        frozen = _get_frozen_gold_bar_quantity(manor)
        available = max(0, int(locked.quantity or 0) - frozen)
        if available < consume_amount:
            raise InsufficientStockError(locked.template.name, consume_amount, available)
    consume_inventory_item_locked(locked, consume_amount, allow_frozen_gold_bars=allow_frozen_gold_bars)


def sync_manor_grain(manor: Manor) -> None:
    """
    同步庄园粮食数量，使 Manor.grain 等于仓库中粮食物品的数量。

    藏宝阁中的粮食不计入庄园粮食储量。
    """
    grain_item = InventoryItem.objects.filter(
        manor=manor,
        template__key=GRAIN_ITEM_KEY,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    ).first()

    grain_quantity = grain_item.quantity if grain_item else 0

    if manor.grain != grain_quantity:
        Manor.objects.filter(pk=manor.pk).update(grain=grain_quantity)
        manor.grain = grain_quantity


def sync_warehouse_grain_item_locked(
    manor: Manor,
    *,
    grain_template: ItemTemplate | None = None,
    grain_template_resolved: bool = False,
) -> None:
    """
    同步仓库中的粮食物品数量，使其与 Manor.grain 一致。

    约束：
    - 仅同步仓库（不处理藏宝阁）；
    - 以 Manor.grain 为单一事实来源；
    - 必须在事务中调用。
    """
    _require_atomic_block("sync_warehouse_grain_item_locked")

    if grain_template is None:
        if grain_template_resolved:
            return
        grain_template = ItemTemplate.objects.filter(key=GRAIN_ITEM_KEY).only("id").first()
    elif not grain_template.pk or grain_template.key != GRAIN_ITEM_KEY:
        raise AssertionError("grain_template must be the persisted grain template")
    if not grain_template:
        return

    target_quantity = max(0, int(getattr(manor, "grain", 0) or 0))
    storage_location = InventoryItem.StorageLocation.WAREHOUSE

    grain_item = (
        InventoryItem.objects.select_for_update()
        .filter(
            manor=manor,
            template=grain_template,
            storage_location=storage_location,
        )
        .first()
    )

    if target_quantity <= 0:
        if grain_item:
            grain_item.delete()
        return

    if grain_item:
        if int(grain_item.quantity) != target_quantity:
            InventoryItem.objects.filter(pk=grain_item.pk).update(quantity=target_quantity, updated_at=Now())
            grain_item.quantity = target_quantity
        return

    try:
        InventoryItem.objects.create(
            manor=manor,
            template=grain_template,
            storage_location=storage_location,
            quantity=target_quantity,
        )
    except IntegrityError:
        InventoryItem.objects.filter(
            manor=manor,
            template=grain_template,
            storage_location=storage_location,
        ).update(quantity=target_quantity, updated_at=Now())


def sync_warehouse_grain_item(manor: Manor) -> None:
    """
    同步仓库粮食物品数量（事务包装）。
    """
    with transaction.atomic():
        locked_manor = Manor.objects.select_for_update().only("id", "grain").get(pk=manor.pk)
        sync_warehouse_grain_item_locked(locked_manor)
    manor.refresh_from_db(fields=["grain"])


def list_inventory_items(manor: Manor):
    """获取庄园的背包物品列表。"""
    return manor.inventory_items.select_related("template").order_by("template__name")


def get_item_quantity(manor: Manor, item_key: str) -> int:
    """
    获取庄园仓库中指定物品的数量（只统计仓库，不含藏宝阁）。
    """
    item = InventoryItem.objects.filter(
        manor=manor,
        template__key=item_key,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    ).first()
    return item.quantity if item else 0


def add_item_to_inventory(
    manor: Manor,
    item_key: str,
    quantity: int = 1,
    storage_location: str = InventoryItem.StorageLocation.WAREHOUSE,
) -> InventoryItem:
    """向庄园背包添加物品。"""
    with transaction.atomic():
        return add_item_to_inventory_locked(
            manor,
            item_key=item_key,
            quantity=quantity,
            storage_location=storage_location,
        )


# 物品效果处理器类型（在 use.py 中实现）
ItemEffectHandler = Callable[[InventoryItem], Dict[str, Any]]


def consume_inventory_item(item_or_manor, item_key_or_amount=1, amount: int = 1) -> None:
    """
    消耗背包物品。

    支持两种调用方式：
    1. consume_inventory_item(item, amount) - 直接传入物品对象
    2. consume_inventory_item(manor, item_key, amount) - 传入庄园和物品key
    """
    consume_amount = int(item_key_or_amount) if isinstance(item_key_or_amount, int) else int(amount or 1)
    if consume_amount <= 0:
        return

    # 方式1: consume_inventory_item(item, amount)
    if isinstance(item_or_manor, InventoryItem):
        item_id = item_or_manor.pk
        item_name = getattr(getattr(item_or_manor, "template", None), "name", "物品")
        if not item_id:
            raise ItemNotFoundError()
        with transaction.atomic():
            try:
                locked = InventoryItem.objects.select_for_update().select_related("template", "manor").get(pk=item_id)
            except InventoryItem.DoesNotExist:
                raise InsufficientStockError(item_name, consume_amount, 0)
            consume_inventory_item_locked(locked, consume_amount)
        return

    # 方式2: consume_inventory_item(manor, item_key, amount)
    if isinstance(item_or_manor, Manor):
        manor = item_or_manor
        item_key = str(item_key_or_amount)
        with transaction.atomic():
            consume_inventory_item_for_manor_locked(manor, item_key, consume_amount)
        return

    raise TypeError("第一个参数必须是 InventoryItem 或 Manor 对象")
