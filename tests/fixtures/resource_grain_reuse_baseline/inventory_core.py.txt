"""
Inventory row operations: add / consume / query.

These functions are intentionally kept free of "item use" business logic, which
lives in `gameplay.services.inventory.use`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable, Dict

from django.db import transaction
from django.db.models import F, IntegerField, Sum, Value
from django.db.models.functions import Coalesce, Now

from core.exceptions import GameError, InsufficientStockError, ItemNotFoundError
from gameplay.models import InventoryItem, ItemTemplate, Manor

# 粮食物品模板 key
GRAIN_ITEM_KEY = "grain"
TREASURY_BLOCKED_ITEM_KEYS = frozenset({GRAIN_ITEM_KEY, "chunqiu_coin"})
_TRANSIENT_GRAIN_PROJECTION_UNSET = object()


def _require_atomic_block(name: str) -> None:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError(f"{name} must be called inside transaction.atomic()")


def _resolve_grain_template(
    grain_template: ItemTemplate | None,
    *,
    grain_template_resolved: bool,
) -> ItemTemplate | None:
    if grain_template is not None:
        if not grain_template.pk or grain_template.key != GRAIN_ITEM_KEY:
            raise AssertionError("grain_template must be the persisted grain template")
        return grain_template
    if grain_template_resolved:
        # 调用方已经完成模板解析且结果为空时，明确表示当前环境没有粮食模板。
        # 不能再次查库，否则会破坏批量规划路径的查询预算。
        return None
    return ItemTemplate.objects.filter(key=GRAIN_ITEM_KEY).only("id", "key").first()


def get_warehouse_grain_quantity(manor: Manor) -> int:
    """读取仓库粮食账本；旧庄园尚未建行时临时回退到兼容字段。"""
    # 选择器单元测试及部分离线规划会传入轻量对象；这类对象没有可查询的
    # 外键身份，只能使用已经注入的仓库值或兼容字段。
    if not isinstance(manor, Manor):
        return max(
            0,
            int(
                getattr(
                    manor,
                    "warehouse_grain_quantity",
                    getattr(manor, "grain", 0),
                )
                or 0
            ),
        )
    projected_quantity = getattr(manor, "warehouse_grain_quantity", _TRANSIENT_GRAIN_PROJECTION_UNSET)
    if projected_quantity is not _TRANSIENT_GRAIN_PROJECTION_UNSET:
        if isinstance(projected_quantity, bool) or not isinstance(projected_quantity, int):
            raise TypeError("warehouse grain projection must be an integer")
        return max(0, projected_quantity)
    quantity = (
        InventoryItem.objects.filter(
            manor=manor,
            template__key=GRAIN_ITEM_KEY,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )
        .values_list("quantity", flat=True)
        .first()
    )
    if quantity is None:
        return max(0, int(getattr(manor, "grain", 0) or 0))
    return max(0, int(quantity or 0))


def clear_warehouse_grain_projection(manor: Manor) -> None:
    """Remove the in-memory read projection before the object is reused."""
    manor.__dict__.pop("warehouse_grain_quantity", None)


def get_warehouse_grain_quantity_locked(
    manor: Manor,
    *,
    grain_template: ItemTemplate | None = None,
    grain_template_resolved: bool = False,
) -> int:
    """在已锁定 Manor 的事务中读取并修复仓库粮食账本。

    调用方必须已在当前事务内持有 Manor 行锁；兼容分支的 get_or_create
    依赖该行锁串行化同一庄园的并发访问，避免重复创建粮食账本行。
    """
    _require_atomic_block("get_warehouse_grain_quantity_locked")
    template = _resolve_grain_template(
        grain_template,
        grain_template_resolved=grain_template_resolved,
    )
    grain_item = None
    if template is not None:
        grain_item = (
            InventoryItem.objects.select_for_update()
            .filter(
                manor=manor,
                template=template,
                storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            )
            .first()
        )

    if grain_item is not None:
        quantity = max(0, int(grain_item.quantity or 0))
    else:
        # 仅用于一次性兼容旧数据；写路径会立即建立仓库账本行。
        legacy_quantity = (
            Manor.objects.filter(pk=manor.pk).values_list("grain", flat=True).first()
            if manor.pk
            else getattr(manor, "grain", 0)
        )
        quantity = max(0, int(legacy_quantity or 0))
        if template is not None and quantity > 0:
            grain_item, _created = InventoryItem.objects.get_or_create(
                manor=manor,
                template=template,
                storage_location=InventoryItem.StorageLocation.WAREHOUSE,
                defaults={"quantity": quantity},
            )
            grain_item = InventoryItem.objects.select_for_update().get(pk=grain_item.pk)
            quantity = max(0, int(grain_item.quantity or 0))

    _set_manor_grain_compatibility(manor, quantity)
    return quantity


def _set_manor_grain_compatibility(manor: Manor, quantity: int) -> None:
    """在同一事务中更新旧字段，禁止它再成为独立业务账本。"""
    normalized_quantity = max(0, int(quantity))
    if int(getattr(manor, "grain", 0) or 0) != normalized_quantity:
        Manor.objects.filter(pk=manor.pk).update(grain=normalized_quantity)
    manor.grain = normalized_quantity
    setattr(manor, "warehouse_grain_quantity", normalized_quantity)


def set_warehouse_grain_quantity_locked(
    manor: Manor,
    quantity: int,
    *,
    grain_template: ItemTemplate | None = None,
    grain_template_resolved: bool = False,
) -> int:
    """在 Manor 锁内设置仓库粮食数量，并原子维护旧兼容字段。

    调用方必须已在当前事务内持有 Manor 行锁；本函数不会自行加锁。
    """
    _require_atomic_block("set_warehouse_grain_quantity_locked")
    normalized_quantity = max(0, int(quantity))
    template = _resolve_grain_template(
        grain_template,
        grain_template_resolved=grain_template_resolved,
    )
    if template is None:
        _set_manor_grain_compatibility(manor, normalized_quantity)
        return normalized_quantity

    if normalized_quantity <= 0:
        grain_item = (
            InventoryItem.objects.select_for_update()
            .filter(
                manor=manor,
                template=template,
                storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            )
            .first()
        )
        if grain_item is not None:
            grain_item.delete()
    else:
        grain_item, _created = InventoryItem.objects.select_for_update().get_or_create(
            manor=manor,
            template=template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            defaults={"quantity": normalized_quantity},
        )
        if int(grain_item.quantity or 0) != normalized_quantity:
            InventoryItem.objects.filter(pk=grain_item.pk).update(quantity=normalized_quantity, updated_at=Now())
            grain_item.quantity = normalized_quantity

    _set_manor_grain_compatibility(manor, normalized_quantity)
    return normalized_quantity


def adjust_warehouse_grain_quantity_locked(
    manor: Manor,
    delta: int,
    *,
    grain_template: ItemTemplate | None = None,
    grain_template_resolved: bool = False,
) -> int:
    """在同一锁内增减仓库粮食，禁止出现负数。"""
    _require_atomic_block("adjust_warehouse_grain_quantity_locked")
    current_quantity = get_warehouse_grain_quantity_locked(
        manor,
        grain_template=grain_template,
        grain_template_resolved=grain_template_resolved,
    )
    target_quantity = current_quantity + int(delta)
    if target_quantity < 0:
        raise InsufficientStockError("粮食", abs(int(delta)), current_quantity)
    return set_warehouse_grain_quantity_locked(
        manor,
        target_quantity,
        grain_template=grain_template,
        grain_template_resolved=grain_template_resolved,
    )


def get_warehouse_used_space(manor: Manor) -> int:
    """Return warehouse item space currently occupied by this manor.

    This read-only helper is retained for compatibility and accounting callers.
    Ordinary warehouse writes intentionally do not enforce a capacity limit.
    Grain is included because its warehouse ledger is represented by an
    InventoryItem row as well.
    """
    total = (
        InventoryItem.objects.filter(
            manor=manor,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )
        .aggregate(
            total=Coalesce(
                Sum(
                    F("quantity") * F("template__storage_space"),
                    output_field=IntegerField(),
                ),
                Value(0),
            )
        )
        .get("total")
    )
    normalized_total = max(0, int(total or 0))

    # A small number of pre-ledger manors may still have grain only on the
    # compatibility Manor.grain field.  Count it so read-only space accounting
    # remains accurate until the first grain write materializes its row.
    grain_row = (
        InventoryItem.objects.filter(
            manor=manor,
            template__key=GRAIN_ITEM_KEY,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )
        .values_list("quantity", "template__storage_space")
        .first()
    )
    if grain_row is None:
        grain_space = ItemTemplate.objects.filter(key=GRAIN_ITEM_KEY).values_list("storage_space", flat=True).first()
        if grain_space is not None:
            legacy_quantity = (
                Manor.objects.filter(pk=manor.pk).values_list("grain", flat=True).first()
                if manor.pk
                else getattr(manor, "grain", 0)
            )
            normalized_total += max(0, int(legacy_quantity or 0)) * max(0, int(grain_space or 0))

    return normalized_total


def add_items_to_inventory_locked(
    manor: Manor,
    grants: Mapping[str, int],
    storage_location: str = InventoryItem.StorageLocation.WAREHOUSE,
    *,
    templates: Mapping[str, ItemTemplate] | None = None,
) -> dict[str, InventoryItem]:
    """Atomically add several inventory quantities under the Manor lock.

    This is the batch counterpart to ``add_item_to_inventory_locked``.  It
    centralizes the unique-row upsert so high-frequency reward paths do not
    implement their own ``get_or_create``/``bulk_create`` variants.
    """
    _require_atomic_block("add_items_to_inventory_locked")
    if not isinstance(grants, Mapping):
        raise AssertionError("grants must be a mapping")

    normalized_grants: dict[str, int] = {}
    for raw_key, raw_quantity in grants.items():
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise AssertionError(f"invalid inventory item key: {raw_key!r}")
        key = raw_key.strip()
        if isinstance(raw_quantity, bool):
            raise AssertionError(f"invalid inventory quantity: {(key, raw_quantity)!r}")
        try:
            quantity = int(raw_quantity)
        except (TypeError, ValueError) as exc:
            raise AssertionError(f"invalid inventory quantity: {(key, raw_quantity)!r}") from exc
        if quantity <= 0:
            raise AssertionError("add_item_to_inventory_locked requires positive quantity")
        normalized_grants[key] = normalized_grants.get(key, 0) + quantity

    if not normalized_grants:
        return {}

    supplied_templates = dict(templates or {})
    resolved_templates: dict[str, ItemTemplate] = {}
    missing_keys: set[str] = set()
    for key in normalized_grants:
        supplied = supplied_templates.get(key)
        if supplied is None:
            missing_keys.add(key)
            continue
        if not supplied.pk or supplied.key != key:
            raise AssertionError("template must match item_key")
        resolved_templates[key] = supplied

    if missing_keys:
        resolved_templates.update(
            {template.key: template for template in ItemTemplate.objects.filter(key__in=missing_keys)}
        )
    missing_templates = set(normalized_grants) - set(resolved_templates)
    if missing_templates:
        raise ItemNotFoundError("物品不存在", item_key=sorted(missing_templates)[0])

    if storage_location == InventoryItem.StorageLocation.TREASURY:
        blocked = set(normalized_grants) & TREASURY_BLOCKED_ITEM_KEYS
        if blocked:
            template = resolved_templates[sorted(blocked)[0]]
            raise GameError(f"{template.name}不可存入藏宝阁")

    warehouse_grain_template = resolved_templates.get(GRAIN_ITEM_KEY)
    if storage_location == InventoryItem.StorageLocation.WAREHOUSE and warehouse_grain_template is not None:
        # Materialize the compatibility row before the grain ledger update so
        # legacy grain-only manors are upgraded consistently.
        get_warehouse_grain_quantity_locked(
            manor,
            grain_template=warehouse_grain_template,
            grain_template_resolved=True,
        )

    for key, quantity in normalized_grants.items():
        template = resolved_templates[key]
        if key == GRAIN_ITEM_KEY and storage_location == InventoryItem.StorageLocation.WAREHOUSE:
            adjust_warehouse_grain_quantity_locked(
                manor,
                quantity,
                grain_template=template,
                grain_template_resolved=True,
            )
            continue

        # get_or_create owns its INSERT savepoint.  If a concurrent request
        # wins the unique key race, Django re-reads the row after rolling back
        # only that savepoint; the surrounding business transaction remains
        # usable.  The increment itself is still an F-expression so it cannot
        # lose a quantity update.
        item, created = InventoryItem.objects.get_or_create(
            manor=manor,
            template=template,
            storage_location=storage_location,
            defaults={"quantity": quantity},
        )
        if not created:
            InventoryItem.objects.filter(pk=item.pk).update(
                quantity=F("quantity") + quantity,
                updated_at=Now(),
            )

    rows = InventoryItem.objects.select_related("template").filter(
        manor=manor,
        template__key__in=tuple(normalized_grants),
        storage_location=storage_location,
    )
    return {row.template.key: row for row in rows}


def add_item_to_inventory_locked(
    manor: Manor,
    item_key: str,
    quantity: int = 1,
    storage_location: str = InventoryItem.StorageLocation.WAREHOUSE,
    *,
    template: ItemTemplate | None = None,
) -> InventoryItem:
    """
    向庄园背包添加物品（调用方必须在 transaction.atomic 中持有 Manor 行锁）。

    该函数不会创建新的事务块；适用于上层服务函数已处于事务中并希望避免嵌套事务的冗余开销。
    """
    _require_atomic_block("add_item_to_inventory_locked")
    item_key = str(item_key).strip()
    item = add_items_to_inventory_locked(
        manor,
        {item_key: quantity},
        storage_location=storage_location,
        templates={item_key: template} if template is not None else None,
    ).get(item_key)
    if not item:
        raise RuntimeError("failed to create or update inventory item")

    return item


def consume_inventory_item_locked(
    locked_item: InventoryItem,
    amount: int = 1,
    *,
    allow_frozen_gold_bars: bool = False,
) -> None:
    """
    消耗背包物品（调用方必须在当前事务内已按 Manor -> InventoryItem 顺序持锁）。

    传入的 item 行必须已由调用方锁定。粮食仓库分支会额外保留 Manor 行锁，
    这是既有兼容路径；新代码应优先使用 consume_inventory_item_for_manor_locked()
    以统一先锁 Manor 再锁 InventoryItem 的顺序。
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

    if (
        locked_item.template.key == GRAIN_ITEM_KEY
        and locked_item.storage_location == InventoryItem.StorageLocation.WAREHOUSE
    ):
        locked_manor = Manor.objects.select_for_update().get(pk=locked_item.manor_id)
        current_quantity = get_warehouse_grain_quantity_locked(
            locked_manor,
            grain_template=locked_item.template,
            grain_template_resolved=True,
        )
        if current_quantity < consume_amount:
            raise InsufficientStockError(item_name, consume_amount, current_quantity)
        set_warehouse_grain_quantity_locked(
            locked_manor,
            current_quantity - consume_amount,
            grain_template=locked_item.template,
            grain_template_resolved=True,
        )
        locked_item.quantity = max(0, current_quantity - consume_amount)
        loaded_manor = getattr(locked_item, "manor", None)
        if loaded_manor is not None and int(getattr(loaded_manor, "pk", 0) or 0) == int(locked_manor.pk):
            loaded_manor.grain = locked_manor.grain
            setattr(loaded_manor, "warehouse_grain_quantity", locked_manor.grain)
        return

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
    按物品 key 消耗庄园仓库物品（在事务内先锁 Manor，再锁库存行，不创建新事务块）。
    """
    _require_atomic_block("consume_inventory_item_for_manor_locked")
    locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
    consume_amount = int(amount or 1)
    if consume_amount <= 0:
        return
    locked = (
        InventoryItem.objects.select_for_update()
        .select_related("template", "manor")
        .filter(
            manor=locked_manor,
            template__key=str(item_key),
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )
        .first()
    )
    if not locked:
        template = ItemTemplate.objects.filter(key=str(item_key)).only("name").first()
        raise InsufficientStockError(template.name if template else str(item_key), consume_amount, 0)
    if str(item_key) == "gold_bar" and not allow_frozen_gold_bars:
        frozen = _get_frozen_gold_bar_quantity(locked_manor)
        available = max(0, int(locked.quantity or 0) - frozen)
        if available < consume_amount:
            raise InsufficientStockError(locked.template.name, consume_amount, available)
    consume_inventory_item_locked(locked, consume_amount, allow_frozen_gold_bars=allow_frozen_gold_bars)
    if str(item_key) == GRAIN_ITEM_KEY:
        manor.grain = locked_manor.grain
        setattr(manor, "warehouse_grain_quantity", locked_manor.grain)


def list_inventory_items(manor: Manor):
    """获取庄园的背包物品列表。"""
    return manor.inventory_items.select_related("template").order_by("template__name")


def get_item_quantity(manor: Manor, item_key: str) -> int:
    """
    获取庄园仓库中指定物品的数量（只统计仓库，不含藏宝阁）。
    """
    if item_key == GRAIN_ITEM_KEY:
        return get_warehouse_grain_quantity(manor)
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
        locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
        return add_item_to_inventory_locked(
            locked_manor,
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
            Manor.objects.select_for_update().get(pk=item_or_manor.manor_id)
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
