# guilds/services/warehouse.py

from __future__ import annotations

from django.db import transaction
from django.db.models import F, Sum

import guilds.constants as guild_constants
from core.exceptions import GuildWarehouseError
from gameplay.models import InventoryItem, ItemTemplate, Manor, ResourceEvent
from gameplay.services.resources import grant_resources_locked

from ..models import Guild, GuildExchangeLog, GuildMember, GuildWarehouse
from .warehouse_config import get_production_items

PROJECTED_RESOURCE_KEYS = ("silver",)
REAL_GUILD_RESOURCE_ITEM_KEYS = ("grain", "gold_bar")
MANOR_RESOURCE_ITEM_KEYS = ("silver", "grain")
WAREHOUSE_LIST_PROJECTED_RESOURCE_KEYS = PROJECTED_RESOURCE_KEYS + REAL_GUILD_RESOURCE_ITEM_KEYS
PROJECTED_RESOURCE_LABELS = {
    "silver": "银两",
    "grain": "粮食",
    "gold_bar": "金条",
}
WAREHOUSE_ITEM_LABELS = {
    **PROJECTED_RESOURCE_LABELS,
    "red_ruby": "红宝石",
}


def add_item_to_warehouse(guild, item_key, quantity, contribution_cost):
    """
    添加物品到帮会仓库

    Args:
        guild: Guild对象
        item_key: 物品key
        quantity: 数量
        contribution_cost: 兑换成本（贡献度）
    """
    if quantity <= 0:
        raise GuildWarehouseError("产出数量必须为正整数")
    if contribution_cost < 0:
        raise GuildWarehouseError("兑换成本不能为负数")

    warehouse_item, created = GuildWarehouse.objects.get_or_create(
        guild=guild, item_key=item_key, defaults={"contribution_cost": contribution_cost}
    )

    # 使用 F() 表达式避免并发下读-改-写丢失更新
    GuildWarehouse.objects.filter(pk=warehouse_item.pk).update(
        quantity=F("quantity") + quantity,
        contribution_cost=contribution_cost,
        total_produced=F("total_produced") + quantity,
    )


def _is_projected_resource_item(item_key: str) -> bool:
    return item_key in PROJECTED_RESOURCE_KEYS


def _get_projected_resource_quantity(guild, item_key: str) -> int:
    if item_key not in WAREHOUSE_LIST_PROJECTED_RESOURCE_KEYS:
        return 0
    return max(0, int(getattr(guild, item_key, 0) or 0))


def _get_projected_resource_exchange_cost(item_key: str) -> int:
    return max(0, int(guild_constants.CONTRIBUTION_RATES.get(item_key, 0) or 0))


def _is_projected_warehouse_listing_item(item_key: str) -> bool:
    return item_key in WAREHOUSE_LIST_PROJECTED_RESOURCE_KEYS


def _is_real_guild_resource_item(item_key: str) -> bool:
    return item_key in REAL_GUILD_RESOURCE_ITEM_KEYS


def _should_exchange_projected_resource_item(*, guild: Guild, item_key: str) -> bool:
    if item_key in PROJECTED_RESOURCE_KEYS:
        return True
    if item_key not in REAL_GUILD_RESOURCE_ITEM_KEYS:
        return False
    if GuildWarehouse.objects.filter(guild=guild, item_key=item_key, quantity__gt=0).exists():
        return False
    return _get_projected_resource_quantity(guild, item_key) > 0


def _grant_exchanged_item_locked(manor, item_key: str, template, quantity: int) -> None:
    if item_key in MANOR_RESOURCE_ITEM_KEYS:
        credited, _overflow = grant_resources_locked(
            manor,
            {item_key: quantity},
            note="帮会仓库兑换",
            reason=ResourceEvent.Reason.TASK_REWARD,
            sync_production=False,
        )
        if int(credited.get(item_key, 0) or 0) < quantity:
            raise GuildWarehouseError("庄园容量不足，兑换失败")
        return

    _grant_inventory_item_to_manor_locked(manor, template, quantity)


def _grant_inventory_item_to_manor_locked(manor, template, quantity: int) -> None:
    inventory_item = (
        InventoryItem.objects.select_for_update()
        .filter(
            manor=manor,
            template=template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )
        .first()
    )

    if inventory_item:
        InventoryItem.objects.filter(pk=inventory_item.pk).update(quantity=F("quantity") + quantity)
        return

    InventoryItem.objects.create(
        manor=manor,
        template=template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        quantity=quantity,
    )


def _exchange_projected_resource_item(member, item_key: str, quantity: int) -> None:
    with transaction.atomic():
        # 与帮会捐献保持一致的锁顺序，避免资源互转路径死锁。
        manor_locked = Manor.objects.select_for_update().get(user=member.user)
        guild_locked = Guild.objects.select_for_update().get(pk=member.guild_id)
        member_locked = GuildMember.objects.select_for_update().get(pk=member.pk)

        member_locked.reset_daily_limits()

        if member_locked.daily_exchange_count >= guild_constants.DAILY_EXCHANGE_LIMIT:
            raise GuildWarehouseError(f"今日兑换次数已达上限（{guild_constants.DAILY_EXCHANGE_LIMIT}次）")

        available_quantity = _get_projected_resource_quantity(guild_locked, item_key)
        if available_quantity < quantity:
            raise GuildWarehouseError(f"库存不足，剩余{available_quantity}件")

        total_cost = _get_projected_resource_exchange_cost(item_key) * quantity
        if member_locked.current_contribution < total_cost:
            raise GuildWarehouseError(f"贡献度不足，需要{total_cost}贡献")

        updated_member = GuildMember.objects.filter(pk=member_locked.pk).update(
            current_contribution=F("current_contribution") - total_cost,
            daily_exchange_count=F("daily_exchange_count") + 1,
        )
        if not updated_member:
            raise GuildWarehouseError("贡献度扣除失败，请重试")

        updated_guild = Guild.objects.filter(pk=guild_locked.pk, **{f"{item_key}__gte": quantity}).update(
            **{item_key: F(item_key) - quantity}
        )
        if not updated_guild:
            raise GuildWarehouseError("库存不足，兑换失败")

        template = None
        if item_key not in MANOR_RESOURCE_ITEM_KEYS:
            try:
                template = ItemTemplate.objects.get(key=item_key)
            except ItemTemplate.DoesNotExist as exc:
                raise GuildWarehouseError("物品不存在") from exc
        _grant_exchanged_item_locked(manor_locked, item_key, template, quantity)

        GuildExchangeLog.objects.create(
            guild=guild_locked,
            member=member_locked,
            item_key=item_key,
            quantity=quantity,
            contribution_cost=total_cost,
        )


def get_guild_material_balances(guild) -> dict[str, int]:
    warehouse_totals = {
        row["item_key"]: int(row["total"] or 0)
        for row in (
            GuildWarehouse.objects.filter(guild=guild, item_key__in=REAL_GUILD_RESOURCE_ITEM_KEYS)
            .values("item_key")
            .annotate(total=Sum("quantity"))
        )
    }
    return {
        "silver": max(0, int(getattr(guild, "silver", 0) or 0)),
        "grain": warehouse_totals.get("grain", 0),
        "gold_bar": warehouse_totals.get("gold_bar", 0),
    }


def spend_guild_warehouse_items_locked(guild, item_costs, *, error_prefix: str = "帮会") -> dict[str, int]:
    normalized_costs = {
        item_key: max(0, int(quantity or 0)) for item_key, quantity in item_costs.items() if int(quantity or 0) > 0
    }
    if not normalized_costs:
        return {}

    warehouse_rows = {
        row.item_key: row
        for row in GuildWarehouse.objects.select_for_update().filter(guild=guild, item_key__in=normalized_costs.keys())
    }

    for item_key, quantity in normalized_costs.items():
        available = int(getattr(warehouse_rows.get(item_key), "quantity", 0) or 0)
        if available < quantity:
            item_label = WAREHOUSE_ITEM_LABELS.get(item_key, item_key)
            raise GuildWarehouseError(f"{error_prefix}{item_label}不足，需要{quantity}")

    for item_key, quantity in normalized_costs.items():
        warehouse_row = warehouse_rows[item_key]
        updated = GuildWarehouse.objects.filter(pk=warehouse_row.pk, quantity__gte=quantity).update(
            quantity=F("quantity") - quantity,
            total_exchanged=F("total_exchanged") + quantity,
        )
        if not updated:
            item_label = WAREHOUSE_ITEM_LABELS.get(item_key, item_key)
            raise GuildWarehouseError(f"{error_prefix}{item_label}不足，需要{quantity}")
        GuildWarehouse.objects.filter(pk=warehouse_row.pk, quantity=0).delete()

    return normalized_costs


def exchange_item(member, item_key, quantity=1):
    """
    兑换帮会仓库物品（并发安全版本 + 修复字段错误）

    使用数据库行锁和F()表达式确保并发安全：
    - 锁定Manor并与捐赠路径保持一致的共享锁顺序
    - 锁定GuildMember防止贡献度透支
    - 锁定GuildWarehouse防止物品超卖
    - 锁定InventoryItem防止物品发放时的并发冲突

    修复：将错误的 item_key 字段改为正确的 template 字段

    Args:
        member: GuildMember对象
        item_key: 物品key
        quantity: 兑换数量

    Raises:
        GuildWarehouseError: 验证失败
    """
    if quantity <= 0:
        raise GuildWarehouseError("兑换数量必须为正整数")

    if _should_exchange_projected_resource_item(guild=member.guild, item_key=item_key):
        _exchange_projected_resource_item(member, item_key, quantity)
        return

    from gameplay.models import ItemTemplate

    template = None
    if item_key not in MANOR_RESOURCE_ITEM_KEYS:
        # 验证物品模板是否存在并可用
        try:
            template = ItemTemplate.objects.get(key=item_key)
            if not _is_real_guild_resource_item(item_key) and not template.is_usable:
                raise GuildWarehouseError("此物品不可在仓库使用")
        except ItemTemplate.DoesNotExist:
            raise GuildWarehouseError("物品不存在")

    # 并发安全的事务处理
    # 锁定顺序：Manor -> GuildMember -> GuildWarehouse
    with transaction.atomic():
        # 与帮会捐赠保持一致的共享锁顺序，避免 Manor/GuildMember 交叉等待。
        manor_locked = Manor.objects.select_for_update().get(user=member.user)

        # 步骤1：锁定成员并验证兑换次数和贡献度
        member_locked = GuildMember.objects.select_for_update().get(pk=member.pk)

        # 重置每日限制（必须在锁内执行，避免并发下穿透每日上限）
        member_locked.reset_daily_limits()

        if member_locked.daily_exchange_count >= guild_constants.DAILY_EXCHANGE_LIMIT:
            raise GuildWarehouseError(f"今日兑换次数已达上限（{guild_constants.DAILY_EXCHANGE_LIMIT}次）")

        # 步骤2：锁定仓库物品并验证库存
        warehouse_item = (
            GuildWarehouse.objects.select_for_update().filter(guild=member_locked.guild, item_key=item_key).first()
        )

        if not warehouse_item:
            raise GuildWarehouseError("物品不存在")

        if warehouse_item.quantity < quantity:
            raise GuildWarehouseError(f"库存不足，剩余{warehouse_item.quantity}件")

        # 计算总成本并验证贡献度
        total_cost = warehouse_item.contribution_cost * quantity
        if member_locked.current_contribution < total_cost:
            raise GuildWarehouseError(f"贡献度不足，需要{total_cost}贡献")

        # 步骤3：使用F()表达式扣除贡献度和增加兑换次数
        updated_member = GuildMember.objects.filter(pk=member_locked.pk).update(
            current_contribution=F("current_contribution") - total_cost,
            daily_exchange_count=F("daily_exchange_count") + 1,
        )

        if not updated_member:
            raise GuildWarehouseError("贡献度扣除失败，请重试")

        # 步骤4：使用F()表达式扣除仓库库存并记录兑换量
        # quantity__gte条件确保不会扣成负数
        updated_wh = GuildWarehouse.objects.filter(pk=warehouse_item.pk, quantity__gte=quantity).update(
            quantity=F("quantity") - quantity,
            total_exchanged=F("total_exchanged") + quantity,
        )

        if not updated_wh:
            raise GuildWarehouseError("库存不足，兑换失败")

        # 清理零库存记录（使用条件删除避免竞态条件）
        # 避免：refresh_from_db() 后另一个事务增加了库存，此时删除会丢失数据
        GuildWarehouse.objects.filter(pk=warehouse_item.pk, quantity=0).delete()

        # 步骤5：添加物品到玩家仓库
        # 修复：使用正确的template字段和StorageLocation枚举
        # 并发安全：沿用已持有的Manor锁完成发放
        _grant_exchanged_item_locked(manor_locked, item_key, template, quantity)

        # 步骤6：记录兑换日志
        GuildExchangeLog.objects.create(
            guild=member_locked.guild,
            member=member_locked,
            item_key=item_key,
            quantity=quantity,
            contribution_cost=total_cost,
        )


def _produce_items_from_config(guild, tech_key: str, tech_level: int):
    """
    通用科技产出函数（使用YAML配置）

    Args:
        guild: Guild对象
        tech_key: 科技标识符（equipment/experience/resource）
        tech_level: 科技等级
    """
    items = get_production_items(tech_key, tech_level)
    for item in items:
        add_item_to_warehouse(guild, item.item_key, item.quantity, item.contribution_cost)


def produce_equipment(guild, tech_level):
    """
    装备锻造科技产出装备

    Args:
        guild: Guild对象
        tech_level: 科技等级
    """
    _produce_items_from_config(guild, "equipment", tech_level)


def produce_experience_items(guild, tech_level):
    """
    经验炼制科技产出经验道具

    Args:
        guild: Guild对象
        tech_level: 科技等级
    """
    _produce_items_from_config(guild, "experience", tech_level)


def produce_resource_packs(guild, tech_level):
    """
    资源补给科技产出资源包

    Args:
        guild: Guild对象
        tech_level: 科技等级
    """
    _produce_items_from_config(guild, "resource", tech_level)


def _build_projected_resource_item(guild, item_key, template):
    quantity = _get_projected_resource_quantity(guild, item_key)
    if quantity <= 0:
        return None
    projected_item = GuildWarehouse(
        guild=guild,
        item_key=item_key,
        quantity=0,
        contribution_cost=_get_projected_resource_exchange_cost(item_key),
        total_produced=0,
        total_exchanged=0,
    )
    projected_item.template = template or ItemTemplate(
        key=item_key,
        name=PROJECTED_RESOURCE_LABELS.get(item_key, item_key),
        is_usable=True,
    )
    projected_item.display_name = PROJECTED_RESOURCE_LABELS.get(item_key, item_key)
    projected_item.is_usable = True
    projected_item.display_quantity = quantity
    projected_item.is_projected = True
    return projected_item


def get_warehouse_items(guild, page=1, per_page=50):
    """
    获取帮会仓库物品列表，附加ItemTemplate信息（N+1查询优化版本 + 分页）

    Args:
        guild: Guild对象
        page: 当前页码（从1开始）
        per_page: 每页数量，默认50

    Returns:
        dict: 包含分页信息和物品列表的字典
            - items: 当前页物品列表
            - page: 当前页码
            - total_pages: 总页数
            - total_count: 总数量
            - has_previous: 是否有上一页
            - has_next: 是否有下一页
    """
    from django.core.paginator import Paginator

    from gameplay.utils.template_loader import get_item_templates_by_keys

    warehouse_items = list(
        GuildWarehouse.objects.filter(guild=guild, quantity__gt=0).order_by("-contribution_cost", "item_key")
    )

    existing_keys = {item.item_key for item in warehouse_items}
    for item_key in WAREHOUSE_LIST_PROJECTED_RESOURCE_KEYS:
        if item_key in existing_keys:
            continue
        projected_item = _build_projected_resource_item(guild, item_key, None)
        if projected_item is not None:
            warehouse_items.append(projected_item)

    warehouse_items.sort(key=lambda item: (-item.contribution_cost, item.item_key))

    paginator = Paginator(warehouse_items, per_page)
    page_obj = paginator.get_page(page)
    page_items = list(page_obj)

    # 查询2：批量预加载当前页需要的ItemTemplate，避免逐个查询
    item_keys = {item.item_key for item in page_items}
    templates_dict = get_item_templates_by_keys(item_keys)

    # 在内存中关联模板信息
    for item in page_items:
        if not getattr(item, "template", None):
            item.template = templates_dict.get(item.item_key)
        item.display_name = getattr(
            item,
            "display_name",
            PROJECTED_RESOURCE_LABELS.get(item.item_key, item.template.name if item.template else item.item_key),
        )
        item.display_quantity = max(0, int(getattr(item, "quantity", 0) or 0))
        if getattr(item, "is_projected", False):
            item.display_quantity = _get_projected_resource_quantity(guild, item.item_key)
            item.contribution_cost = _get_projected_resource_exchange_cost(item.item_key)
            item.is_usable = True
            continue
        if _is_real_guild_resource_item(item.item_key):
            item.is_usable = True
            continue
        # 如果找不到模板，标记为不可用（防止幽灵物品被兑换）
        item.is_usable = item.template.is_usable if item.template else False

    return {
        "items": page_items,
        "page": page_obj.number,
        "total_pages": paginator.num_pages,
        "total_count": paginator.count,
        "has_previous": page_obj.has_previous(),
        "has_next": page_obj.has_next(),
        "previous_page": page_obj.previous_page_number() if page_obj.has_previous() else None,
        "next_page": page_obj.next_page_number() if page_obj.has_next() else None,
    }


def get_exchange_logs(guild, limit=50):
    """
    获取兑换日志

    Args:
        guild: Guild对象
        limit: 返回数量

    Returns:
        QuerySet
    """
    return (
        GuildExchangeLog.objects.filter(guild=guild)
        .select_related("member__user__manor")
        .order_by("-exchanged_at")[:limit]
    )
