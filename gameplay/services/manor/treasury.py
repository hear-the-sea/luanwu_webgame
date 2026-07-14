"""
藏宝阁相关业务逻辑
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import F

from core.exceptions import BuildingNotFoundError, InsufficientSpaceError, InsufficientStockError, ItemNotFoundError

from ...constants import BUILDING_MAX_LEVELS, BuildingKeys
from ...models import InventoryItem, Manor

# 粮食物品模板 key
GRAIN_ITEM_KEY = "grain"


def get_treasury_capacity(manor: Manor) -> int:
    """
    获取藏宝阁容量

    Args:
        manor: 庄园对象

    Returns:
        藏宝阁容量（初始500，每级+500，建筑最高20级，满级10000）
    """
    treasury = manor.buildings.select_related("building_type").filter(building_type__key=BuildingKeys.TREASURY).first()

    if not treasury:
        return 0

    level = min(treasury.level, BUILDING_MAX_LEVELS[BuildingKeys.TREASURY])
    return 500 + max(0, level - 1) * 500


def get_treasury_used_space(manor: Manor) -> int:
    """
    获取藏宝阁已使用空间

    Args:
        manor: 庄园对象

    Returns:
        已使用空间
    """
    items = InventoryItem.objects.filter(
        manor=manor, storage_location=InventoryItem.StorageLocation.TREASURY
    ).select_related("template")

    total_space = 0
    for item in items:
        total_space += item.template.storage_space * item.quantity

    return total_space


def get_warehouse_used_space(manor: Manor) -> int:
    """
    获取仓库已使用空间

    Args:
        manor: 庄园对象

    Returns:
        已使用空间
    """
    items = InventoryItem.objects.filter(
        manor=manor, storage_location=InventoryItem.StorageLocation.WAREHOUSE
    ).select_related("template")

    total_space = 0
    for item in items:
        total_space += item.template.storage_space * item.quantity

    return total_space


@transaction.atomic
def move_item_to_treasury(manor: Manor, item_id: int, quantity: int) -> None:
    """
    将物品从仓库移动到藏宝阁

    Args:
        manor: 庄园对象
        item_id: 物品ID
        quantity: 移动数量

    Raises:
        BuildingNotFoundError: 藏宝阁未建造时抛出
        ItemNotFoundError: 物品不存在时抛出
        InsufficientStockError: 物品数量不足时抛出
        InsufficientSpaceError: 藏宝阁空间不足时抛出
    """
    if quantity <= 0:
        raise AssertionError("move_item_to_treasury requires positive quantity")
    manor = Manor.objects.select_for_update().get(pk=manor.pk)
    # 检查藏宝阁是否存在
    treasury_capacity = get_treasury_capacity(manor)
    if treasury_capacity == 0:
        raise BuildingNotFoundError(BuildingKeys.TREASURY)

    # 获取仓库中的物品
    warehouse_item = (
        InventoryItem.objects.select_for_update()
        .filter(id=item_id, manor=manor, storage_location=InventoryItem.StorageLocation.WAREHOUSE)
        .select_related("template")
        .first()
    )

    if not warehouse_item:
        raise ItemNotFoundError("物品不存在或不在仓库中")

    # 金条需要特殊检查：考虑拍卖冻结的金条
    if warehouse_item.template.key == "gold_bar":
        from trade.services.auction_service import get_frozen_gold_bars

        frozen = get_frozen_gold_bars(manor)
        available = warehouse_item.quantity - frozen
        if available < quantity:
            raise InsufficientStockError(warehouse_item.template.name, quantity, available)
    elif warehouse_item.quantity < quantity:
        raise InsufficientStockError(warehouse_item.template.name, quantity, warehouse_item.quantity)

    # 检查藏宝阁空间是否足够
    item_space = warehouse_item.template.storage_space * quantity
    treasury_used = get_treasury_used_space(manor)

    if treasury_used + item_space > treasury_capacity:
        raise InsufficientSpaceError("treasury", treasury_capacity - treasury_used, item_space)

    # 检查是否是粮食物品
    is_grain = warehouse_item.template.key == GRAIN_ITEM_KEY

    # 减少仓库中的物品数量
    warehouse_item.quantity -= quantity
    if warehouse_item.quantity == 0:
        warehouse_item.delete()
    else:
        warehouse_item.save(update_fields=["quantity"])

    # 增加藏宝阁中的物品数量
    treasury_item, created = InventoryItem.objects.get_or_create(
        manor=manor,
        template=warehouse_item.template,
        storage_location=InventoryItem.StorageLocation.TREASURY,
        defaults={"quantity": 0},
    )

    if not created:
        treasury_item = InventoryItem.objects.select_for_update().get(pk=treasury_item.pk)

    treasury_item.quantity += quantity
    treasury_item.save(update_fields=["quantity"])

    # 粮食移入藏宝阁时，减少 Manor.grain
    if is_grain:
        Manor.objects.filter(pk=manor.pk).update(grain=F("grain") - quantity)
        manor.grain = max(0, getattr(manor, "grain", 0) - quantity)


@transaction.atomic
def move_item_to_warehouse(manor: Manor, item_id: int, quantity: int) -> None:
    """
    将物品从藏宝阁移动到仓库

    Args:
        manor: 庄园对象
        item_id: 物品ID
        quantity: 移动数量

    Raises:
        ItemNotFoundError: 物品不存在时抛出
        InsufficientStockError: 物品数量不足时抛出
    """
    if quantity <= 0:
        raise AssertionError("move_item_to_warehouse requires positive quantity")
    manor = Manor.objects.select_for_update().get(pk=manor.pk)
    # 获取藏宝阁中的物品
    treasury_item = (
        InventoryItem.objects.select_for_update()
        .filter(id=item_id, manor=manor, storage_location=InventoryItem.StorageLocation.TREASURY)
        .select_related("template")
        .first()
    )

    if not treasury_item:
        raise ItemNotFoundError("物品不存在或不在藏宝阁中")

    if treasury_item.quantity < quantity:
        raise InsufficientStockError(treasury_item.template.name, quantity, treasury_item.quantity)

    # 检查是否是粮食物品
    is_grain = treasury_item.template.key == GRAIN_ITEM_KEY

    # 减少藏宝阁中的物品数量
    treasury_item.quantity -= quantity
    if treasury_item.quantity == 0:
        treasury_item.delete()
    else:
        treasury_item.save(update_fields=["quantity"])

    # 增加仓库中的物品数量
    warehouse_item, created = InventoryItem.objects.get_or_create(
        manor=manor,
        template=treasury_item.template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        defaults={"quantity": 0},
    )

    if not created:
        warehouse_item = InventoryItem.objects.select_for_update().get(pk=warehouse_item.pk)

    warehouse_item.quantity += quantity
    warehouse_item.save(update_fields=["quantity"])

    # 粮食移回仓库时，增加 Manor.grain
    if is_grain:
        Manor.objects.filter(pk=manor.pk).update(grain=F("grain") + quantity)
        manor.grain = getattr(manor, "grain", 0) + quantity
