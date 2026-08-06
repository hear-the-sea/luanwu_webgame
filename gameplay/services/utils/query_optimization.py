"""
查询优化工具

提供常用的查询优化函数和prefetch配置。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import Prefetch

if TYPE_CHECKING:
    from django.db.models import QuerySet


def get_manor_with_relations(manor_id: int):
    """
    获取庄园及其常用关联数据（优化查询）。

    Args:
        manor_id: 庄园ID

    Returns:
        带预加载关联的庄园对象
    """
    from gameplay.models import Manor

    return (
        Manor.objects.select_related("user")
        .prefetch_related(
            "buildings__building_type",
            "technologies",
            "guests__template",
            "troops__troop_template",
        )
        .get(pk=manor_id)
    )


def prefetch_guests_with_gear():
    """
    预加载门客及其装备的Prefetch配置。

    Returns:
        Prefetch对象
    """
    from guests.models import Guest

    return Prefetch(
        "guests", queryset=Guest.objects.select_related("template").prefetch_related("gear_items__template")
    )


def prefetch_active_missions():
    """
    预加载进行中任务的Prefetch配置。

    Returns:
        Prefetch对象
    """
    from gameplay.models import MissionRun

    return Prefetch(
        "mission_runs",
        queryset=MissionRun.objects.filter(status=MissionRun.Status.ACTIVE)
        .select_related("mission")
        .prefetch_related("guests"),
        to_attr="active_mission_runs",
    )


def prefetch_upgrading_buildings():
    """
    预加载正在升级建筑的Prefetch配置。

    Returns:
        Prefetch对象
    """
    from gameplay.models import Building

    return Prefetch(
        "buildings",
        queryset=Building.objects.filter(is_upgrading=True, upgrade_complete_at__isnull=False)
        .select_related("building_type")
        .order_by("upgrade_complete_at"),
        to_attr="upgrading_building_list",
    )


def optimize_guest_queryset(queryset: "QuerySet") -> "QuerySet":
    """
    优化门客查询集。

    Args:
        queryset: 原始查询集

    Returns:
        优化后的查询集
    """
    # 首页只展示门客名称和状态；装备、技能属于详情/战斗读模型，
    # 在这里预取会为每次首页访问额外加载两组关系。
    return queryset.select_related("template", "manor")


def optimize_mission_run_queryset(queryset: "QuerySet") -> "QuerySet":
    """
    优化任务运行查询集。

    Args:
        queryset: 原始查询集

    Returns:
        优化后的查询集
    """
    return queryset.select_related("mission", "manor", "battle_report").prefetch_related("guests__template")


def bulk_get_manor_stats(manor_ids: list) -> dict:
    """
    批量获取庄园统计数据。

    Args:
        manor_ids: 庄园ID列表

    Returns:
        {manor_id: {stats}} 字典
    """
    from django.db.models import Count, Sum

    from gameplay.models import Manor

    stats = (
        Manor.objects.filter(id__in=manor_ids)
        .annotate(
            guest_count=Count("guests"),
            total_troops=Sum("troops__count"),
        )
        .values("id", "guest_count", "total_troops", "prestige", "silver", "grain")
    )

    return {s["id"]: s for s in stats}


def get_idle_guests_optimized(manor) -> list:
    """
    获取闲置门客（优化版）。

    Args:
        manor: 庄园对象

    Returns:
        闲置门客列表
    """
    from guests.models import Guest, GuestStatus

    return list(
        Guest.objects.filter(manor=manor, status=GuestStatus.IDLE)
        .select_related("template")
        .only(
            "id",
            "level",
            "status",
            "custom_name",
            "current_hp",
            "hp_bonus",
            "defense_stat",
            "template__name",
            "template__key",
            "template__rarity",
        )
        .order_by("-level", "template__name")
    )


def count_with_cache(queryset, cache_key: str, timeout: int = 60) -> int:
    """
    带缓存的count查询。

    Args:
        queryset: 查询集
        cache_key: 缓存键
        timeout: 缓存超时（秒）

    Returns:
        计数结果
    """
    from django.core.cache import cache

    count = cache.get(cache_key)
    if count is None:
        count = queryset.count()
        cache.set(cache_key, count, timeout=timeout)
    return count
