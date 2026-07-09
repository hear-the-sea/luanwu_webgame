"""
庄园迁移服务

提供庄园迁移、坐标生成等功能。
"""

from __future__ import annotations

import random
from typing import Optional, Tuple

from django.db import transaction
from django.utils import timezone

from core.exceptions import ItemInsufficientError, ItemNotFoundError, RelocationError

from ...constants import REGION_DICT, PVPConstants
from ...models import Manor
from .combat import get_active_raid_count, get_incoming_raids
from .utils import get_asset_level


def get_relocation_cost(manor: Manor) -> int:
    """
    获取庄园迁移所需的金条数量。

    Returns:
        金条数量
    """
    asset_level, _ = get_asset_level(manor)

    if asset_level == "匮乏":
        return PVPConstants.RELOCATION_COST_POOR
    elif asset_level == "一般":
        return PVPConstants.RELOCATION_COST_NORMAL
    elif asset_level == "充裕":
        return PVPConstants.RELOCATION_COST_RICH
    else:  # 富足
        return PVPConstants.RELOCATION_COST_WEALTHY


def relocate_manor(manor: Manor, new_region: str) -> Tuple[int, int]:
    """
    迁移庄园到新地区。

    Args:
        manor: 庄园
        new_region: 新地区编码

    Returns:
        (新X坐标, 新Y坐标)

    Raises:
        RelocationError: 无法迁移时
    """
    # 验证地区
    if new_region not in REGION_DICT:
        raise RelocationError("无效的地区")

    # 检查迁移条件
    if not manor.can_relocate:
        if manor.is_under_newbie_protection:
            raise RelocationError("新手保护期内无法迁移")
        raise RelocationError("迁移冷却中，请稍后再试")

    # 检查是否有出征中的队伍
    active_raids = get_active_raid_count(manor)
    if active_raids > 0:
        raise RelocationError("有出征中的队伍，无法迁移")

    # 检查是否有敌军来袭
    incoming = get_incoming_raids(manor)
    if incoming:
        raise RelocationError("有敌军来袭，无法迁移")

    cost = get_relocation_cost(manor)

    from trade.services.auction.gold_bars import consume_available_gold_bars_locked

    with transaction.atomic():
        locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
        try:
            consume_available_gold_bars_locked(locked_manor, cost)
        except (ItemInsufficientError, ItemNotFoundError) as exc:
            available_gold = exc.context.get("available", 0)
            raise RelocationError(f"可用金条不足，需要 {cost} 个（当前可用 {available_gold} 个）") from exc

        # 生成新坐标（确保唯一）
        new_x, new_y = _generate_unique_coordinate(new_region, exclude_manor_id=manor.id)

        # 更新庄园
        locked_manor.region = new_region
        locked_manor.coordinate_x = new_x
        locked_manor.coordinate_y = new_y
        locked_manor.last_relocation_at = timezone.now()
        locked_manor.save(update_fields=["region", "coordinate_x", "coordinate_y", "last_relocation_at"])

        manor.region = locked_manor.region
        manor.coordinate_x = locked_manor.coordinate_x
        manor.coordinate_y = locked_manor.coordinate_y
        manor.last_relocation_at = locked_manor.last_relocation_at

    return new_x, new_y


def _generate_unique_coordinate(region: str, exclude_manor_id: Optional[int] = None) -> Tuple[int, int]:
    """在指定地区生成唯一坐标"""
    max_attempts = 100

    for _ in range(max_attempts):
        x = random.randint(PVPConstants.COORDINATE_MIN, PVPConstants.COORDINATE_MAX)
        y = random.randint(PVPConstants.COORDINATE_MIN, PVPConstants.COORDINATE_MAX)

        # 检查是否已被占用
        query = Manor.objects.filter(region=region, coordinate_x=x, coordinate_y=y)
        if exclude_manor_id:
            query = query.exclude(id=exclude_manor_id)

        if not query.exists():
            return x, y

    raise RelocationError("无法生成唯一坐标，请稍后重试")
