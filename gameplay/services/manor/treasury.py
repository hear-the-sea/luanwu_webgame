"""
藏宝阁相关业务逻辑
"""

from __future__ import annotations

from django.db import transaction

from core.exceptions import (
    BuildingNotFoundError,
    GameError,
    InsufficientSpaceError,
    InsufficientStockError,
    ItemNotFoundError,
)

from ...constants import BUILDING_MAX_LEVELS, BuildingKeys
from ...models import InventoryItem, ItemTemplate, Manor
from ..inventory.core import TREASURY_BLOCKED_ITEM_KEYS, add_items_to_inventory_locked, consume_inventory_item_locked
from ..inventory.core import get_warehouse_used_space as _get_warehouse_used_space


def get_treasury_deposit_block_reason(template: ItemTemplate) -> str | None:
    """Return the user-facing reason when an item cannot enter the treasury."""
    if template.key not in TREASURY_BLOCKED_ITEM_KEYS:
        return None
    return f"{template.name}不可存入藏宝阁"


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
    """Compatibility export for the inventory-owned warehouse calculation."""
    return _get_warehouse_used_space(manor)


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

    deposit_block_reason = get_treasury_deposit_block_reason(warehouse_item.template)
    if deposit_block_reason:
        raise GameError(deposit_block_reason)

    # 金条需要特殊检查：考虑拍卖冻结的金条
    if warehouse_item.template.key == "gold_bar":
        from trade.services.auction_service import get_frozen_gold_bars

        frozen = get_frozen_gold_bars(manor)
        available = warehouse_item.unbound_quantity - frozen
        if available < quantity:
            raise InsufficientStockError(warehouse_item.template.name, quantity, available)
    elif warehouse_item.unbound_quantity < quantity:
        raise InsufficientStockError(warehouse_item.template.name, quantity, warehouse_item.unbound_quantity)

    # 检查藏宝阁空间是否足够
    item_space = warehouse_item.template.storage_space * quantity
    treasury_used = get_treasury_used_space(manor)

    if treasury_used + item_space > treasury_capacity:
        raise InsufficientSpaceError("treasury", treasury_capacity - treasury_used, item_space)

    # 绑定物品只允许自用，存储移动仅使用非绑定数量。
    template = warehouse_item.template
    bound_moved = consume_inventory_item_locked(warehouse_item, quantity, allow_bound=False)
    add_items_to_inventory_locked(
        manor,
        {template.key: quantity},
        storage_location=InventoryItem.StorageLocation.TREASURY,
        templates={template.key: template},
        bound_quantities={template.key: bound_moved},
        is_bound=False,
    )


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

    if treasury_item.unbound_quantity < quantity:
        raise InsufficientStockError(treasury_item.template.name, quantity, treasury_item.unbound_quantity)

    # 返还仓库同样只允许非绑定数量，包括旧藏宝阁粮食的兼容路径。
    template = treasury_item.template
    bound_moved = consume_inventory_item_locked(treasury_item, quantity, allow_bound=False)
    add_items_to_inventory_locked(
        manor,
        {template.key: quantity},
        templates={template.key: template},
        bound_quantities={template.key: bound_moved},
        is_bound=False,
    )
