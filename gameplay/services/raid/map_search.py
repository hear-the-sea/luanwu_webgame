"""
地图查询服务

提供庄园搜索功能：按名称、地区、坐标搜索。
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from itertools import islice
from typing import Any, Dict, Iterable, List, Optional, Tuple

from django.db.models import Count, QuerySet
from django.utils import timezone

from ...constants import PVPConstants
from ...models import BotProfile, Manor, RaidRun
from ..pvp_runtime.travel import calculate_pvp_travel_time
from .utils import calculate_distance, can_attack_target, get_prestige_color, is_same_region

_SEARCH_BATCH_SIZE = 200
_SEARCH_MANOR_FIELDS = (
    "id",
    "name",
    "prestige",
    "region",
    "coordinate_x",
    "coordinate_y",
    "newbie_protection_until",
    "defeat_protection_until",
    "peace_shield_until",
    "user__username",
)


def _visible_manors() -> QuerySet[Manor]:
    return Manor.objects.exclude(bot_profile__state=BotProfile.State.STALE)


def search_manors_by_name(searcher: Manor, name_query: str, limit: int = 20) -> List[Dict[str, Any]]:
    """
    按庄园名称搜索。

    Args:
        searcher: 搜索者庄园
        name_query: 搜索关键词
        limit: 返回结果上限

    Returns:
        庄园列表（包含距离和声望颜色）
    """
    manors = (
        _visible_manors()
        .filter(name__icontains=name_query)
        .exclude(id=searcher.id)
        .select_related("user")
        .only(*_SEARCH_MANOR_FIELDS)[:limit]
    )

    return _format_manor_list(searcher, manors)


def search_manors_by_region(
    searcher: Manor, region: str, page: int = 1, page_size: int = 20
) -> Tuple[List[Dict[str, Any]], int]:
    """
    按地区查询庄园列表。

    Args:
        searcher: 搜索者庄园
        region: 地区编码
        page: 页码
        page_size: 每页数量

    Returns:
        (庄园列表, 总数)
    """
    # 地区列表应包含自己庄园；当某地区仅有自己时，也能看到对应条目，避免误判“该地区无人”。
    queryset = _visible_manors().filter(region=region).select_related("user").only(*_SEARCH_MANOR_FIELDS)

    total = queryset.count()
    offset = (page - 1) * page_size
    manors = list(queryset[offset : offset + page_size])

    return _format_manor_list(searcher, manors), total


def search_manors_by_coordinate(
    searcher: Manor, center_x: int, center_y: int, radius: float, region: Optional[str] = None, limit: int = 50
) -> List[Dict[str, Any]]:
    """
    按坐标范围搜索庄园。

    Args:
        searcher: 搜索者庄园
        center_x: 中心X坐标
        center_y: 中心Y坐标
        radius: 搜索半径
        region: 地区（可选，不指定则在搜索者所在地区）
        limit: 返回结果上限

    Returns:
        庄园列表
    """
    target_region = region or searcher.region

    # 先用方形范围粗筛
    min_x = max(1, int(center_x - radius))
    max_x = min(999, int(center_x + radius))
    min_y = max(1, int(center_y - radius))
    max_y = min(999, int(center_y + radius))

    manors = (
        _visible_manors()
        .filter(
            region=target_region,
            coordinate_x__gte=min_x,
            coordinate_x__lte=max_x,
            coordinate_y__gte=min_y,
            coordinate_y__lte=max_y,
        )
        .exclude(id=searcher.id)
        .select_related("user")
    )

    # 精确过滤圆形范围
    result = []
    for manor in manors:
        dx = manor.coordinate_x - center_x
        dy = manor.coordinate_y - center_y
        dist = math.sqrt(dx * dx + dy * dy)
        if dist <= radius:
            result.append(manor)
        if len(result) >= limit:
            break

    return _format_manor_list(searcher, result)


def _format_manor_list(searcher: Manor, manors: Iterable[Manor]) -> List[Dict[str, Any]]:
    """
    格式化庄园列表，添加距离和声望颜色信息。
    """
    manors_list = list(manors)
    if not manors_list:
        return []

    # 性能优化：批量计算“目标24小时内被攻击次数”，避免对每个目标 N+1 次 COUNT 查询。
    now = timezone.now()
    cutoff = now - timedelta(hours=24)
    defender_ids = [m.id for m in manors_list]
    recent_attack_counts = {
        row["defender_id"]: row["cnt"]
        for row in (
            RaidRun.objects.filter(defender_id__in=defender_ids, started_at__gte=cutoff)
            .values("defender_id")
            .annotate(cnt=Count("id"))
        )
    }

    result: List[Dict[str, Any]] = []
    for manor in manors_list:
        distance = calculate_distance(searcher, manor)
        color = get_prestige_color(searcher.prestige, manor.prestige)
        can_attack, reason = can_attack_target(
            searcher,
            manor,
            recent_attacks=int(recent_attack_counts.get(manor.id, 0) or 0),
            now=now,
            check_stale_bot=False,
        )

        result.append(
            {
                "id": manor.id,
                "name": manor.display_name,
                "region": manor.region,
                "region_display": manor.region_display,
                "coordinate_x": manor.coordinate_x,
                "coordinate_y": manor.coordinate_y,
                "location_display": manor.location_display,
                "prestige": manor.prestige,
                "prestige_color": color,
                "distance": round(distance, 1),
                "can_attack": can_attack,
                "attack_reason": reason,
                "is_protected": manor.is_protected,
            }
        )

    # 按距离排序
    result.sort(key=lambda x: x["distance"])
    return result


def _count_attackable_manors(queryset: QuerySet[Manor], searcher: Manor, *, now: datetime, limit: int) -> int:
    """Count attackable targets in bounded batches, stopping at the needed threshold."""
    if limit <= 0:
        return 0

    cutoff = now - timedelta(hours=24)
    candidates = queryset.exclude(id=searcher.id).only(*_SEARCH_MANOR_FIELDS).iterator(chunk_size=_SEARCH_BATCH_SIZE)
    count = 0
    while count < limit:
        batch = list(islice(candidates, _SEARCH_BATCH_SIZE))
        if not batch:
            break
        recent_attack_counts = {
            row["defender_id"]: row["cnt"]
            for row in (
                RaidRun.objects.filter(
                    defender_id__in=[manor.id for manor in batch],
                    started_at__gte=cutoff,
                )
                .values("defender_id")
                .annotate(cnt=Count("id"))
            )
        }
        for manor in batch:
            if can_attack_target(
                searcher,
                manor,
                recent_attacks=int(recent_attack_counts.get(manor.id, 0) or 0),
                now=now,
                check_stale_bot=False,
            )[0]:
                count += 1
                if count >= limit:
                    break
    return count


def count_attackable_manors_by_region(searcher: Manor, region: str, *, limit: int) -> int:
    """Count currently attackable targets in one region without mutating state."""
    if limit <= 0:
        return 0
    queryset = _visible_manors().filter(region=region).select_related("user").only(*_SEARCH_MANOR_FIELDS)
    return _count_attackable_manors(
        queryset,
        searcher,
        now=timezone.now(),
        limit=limit,
    )


def get_manor_public_info(manor: Manor, viewer: Optional[Manor] = None) -> Dict[str, Any]:
    """
    获取庄园的公开信息。

    Args:
        manor: 目标庄园
        viewer: 查看者庄园（用于计算距离和声望颜色）

    Returns:
        庄园公开信息
    """
    info = {
        "id": manor.id,
        "name": manor.display_name,
        "region": manor.region,
        "region_display": manor.region_display,
        "coordinate_x": manor.coordinate_x,
        "coordinate_y": manor.coordinate_y,
        "location_display": manor.location_display,
        "prestige": manor.prestige,
        "is_protected": manor.is_protected,
    }

    if viewer:
        distance = calculate_distance(viewer, manor)
        info["distance"] = round(distance, 1)
        info["prestige_color"] = get_prestige_color(viewer.prestige, manor.prestige)
        if manor.prestige > viewer.prestige:
            info["prestige_comparison"] = "higher"
        elif manor.prestige < viewer.prestige:
            info["prestige_comparison"] = "lower"
        else:
            info["prestige_comparison"] = "equal"

        # 基础估算采用160敏捷、1名门客、0护院；配置页会按实际阵容实时修正。
        route_seconds = PVPConstants.RAID_BASE_TRAVEL_TIME + distance * PVPConstants.RAID_TRAVEL_TIME_PER_DISTANCE
        region_factor = 1.0 if is_same_region(viewer, manor) else PVPConstants.RAID_CROSS_REGION_MULTIPLIER
        estimate = calculate_pvp_travel_time(
            route_seconds=route_seconds,
            guests=[],
            troop_loadout={},
            external_factor=region_factor,
        )
        info["travel_route_seconds"] = route_seconds
        info["travel_region_factor"] = region_factor
        info["travel_time"] = estimate.scaled_seconds
        can_attack, reason = can_attack_target(viewer, manor)
        info["can_attack"] = can_attack
        info["attack_reason"] = reason

    return info
