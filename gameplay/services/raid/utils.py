"""
踢馆系统工具函数

提供距离计算、声望颜色、攻击检查等基础工具。
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Optional, Tuple

from django.conf import settings
from django.core.cache import cache
from django.db.models import F, Sum
from django.utils import timezone

from gameplay.services.utils.cache_exceptions import CACHE_INFRASTRUCTURE_EXCEPTIONS

from ...constants import PVPConstants
from ...models import BotProfile, InventoryItem, Manor, RaidRun

logger = logging.getLogger(__name__)


def _recent_attacks_cache_key(defender_id: int) -> str:
    return f"raid:recent_attacks_24h:{int(defender_id)}"


def _recent_attacks_cache_ttl_seconds() -> int:
    raw = getattr(settings, "RAID_RECENT_ATTACKS_CACHE_TTL_SECONDS", 5)
    try:
        ttl = int(raw)
    except (TypeError, ValueError):
        ttl = 5
    return max(1, ttl)


def _safe_cache_get(key: str) -> int | None:
    try:
        return cache.get(key)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        logger.warning("raid utils cache.get failed: key=%s", key, exc_info=True)
        return None


def _safe_cache_set(key: str, value: int, timeout: int) -> None:
    try:
        cache.set(key, int(value), timeout=timeout)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        logger.warning("raid utils cache.set failed: key=%s", key, exc_info=True)


def _safe_cache_delete(key: str) -> None:
    try:
        cache.delete(key)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        logger.warning("raid utils cache.delete failed: key=%s", key, exc_info=True)


def invalidate_recent_attacks_cache(defender_id: int) -> None:
    """Invalidate cached 24h raid-received count for a defender manor."""
    if int(defender_id or 0) <= 0:
        return
    _safe_cache_delete(_recent_attacks_cache_key(int(defender_id)))


def get_recent_attacks_24h(defender: Manor, now: Optional[datetime] = None, *, use_cache: bool = True) -> int:
    """
    Return how many raids the defender received in the last 24 hours.

    Cache is best-effort and should be disabled (`use_cache=False`) for strict checks
    inside critical locked transactions.
    """
    now = now or timezone.now()
    defender_id = int(getattr(defender, "id", 0) or 0)
    cache_key = _recent_attacks_cache_key(defender_id) if defender_id > 0 else ""

    if use_cache and cache_key:
        cached = _safe_cache_get(cache_key)
        if isinstance(cached, int) and cached >= 0:
            return cached

    recent_attacks = RaidRun.objects.filter(defender=defender, started_at__gte=now - timedelta(hours=24)).count()

    if use_cache and cache_key:
        _safe_cache_set(cache_key, recent_attacks, timeout=_recent_attacks_cache_ttl_seconds())
    return int(recent_attacks)


def calculate_distance(manor1: Manor, manor2: Manor) -> float:
    """
    计算两个庄园之间的距离。

    Args:
        manor1: 庄园1
        manor2: 庄园2

    Returns:
        欧几里得距离
    """
    dx = manor1.coordinate_x - manor2.coordinate_x
    dy = manor1.coordinate_y - manor2.coordinate_y
    return math.sqrt(dx * dx + dy * dy)


def is_same_region(manor1: Manor, manor2: Manor) -> bool:
    """判断两个庄园是否在同一地区"""
    return manor1.region == manor2.region


def get_prestige_color(my_prestige: int, target_prestige: int) -> str:
    """
    根据声望差值计算颜色标识。

    Args:
        my_prestige: 我方声望
        target_prestige: 目标声望

    Returns:
        颜色标识: 'green'（弱小）, 'white'（势均力敌）, 'red'（强大）
    """
    protection_range = get_prestige_protection_range(my_prestige, target_prestige)
    if protection_range is None:
        return "white"  # 高声望区间取消声望差保护

    diff = target_prestige - my_prestige
    if diff < -protection_range:
        return "green"
    elif diff > protection_range:
        return "red"
    return "white"  # 势均力敌


def get_prestige_protection_range(my_prestige: int, target_prestige: int) -> int | None:
    """
    Return the allowed prestige gap for raid/scout protection.

    Uses the lower prestige side as the band anchor, so lower-prestige players
    keep protection while both sides are below the high-prestige cutoff.
    """
    if (
        my_prestige >= PVPConstants.RAID_PRESTIGE_PROTECTION_CUTOFF
        and target_prestige >= PVPConstants.RAID_PRESTIGE_PROTECTION_CUTOFF
    ):
        return None

    base_prestige = min(my_prestige, target_prestige)
    for upper_bound, allowed_gap in PVPConstants.RAID_PRESTIGE_DYNAMIC_RANGES:
        if base_prestige < upper_bound:
            return allowed_gap
    return PVPConstants.RAID_PRESTIGE_DYNAMIC_RANGES[-1][1]


def can_attack_target(
    attacker: Manor,
    defender: Manor,
    *,
    recent_attacks: Optional[int] = None,
    now: Optional[datetime] = None,
    use_cached_recent_attacks: bool = True,
    check_defeat_protection: bool = True,
    check_stale_bot: bool = True,
) -> Tuple[bool, str]:
    """
    检查是否可以攻击目标庄园。

    Args:
        attacker: 进攻方庄园
        defender: 防守方庄园
        recent_attacks: 可选的“目标24小时内被攻击次数”预计算值；提供时将跳过数据库 COUNT
        now: 可选的当前时间（用于批量计算时复用）
        use_cached_recent_attacks: 是否允许使用短TTL缓存读取被攻击次数
        check_defeat_protection: 是否检查防守方战败保护（侦察逻辑可关闭）
        check_stale_bot: 是否查询目标的虚拟玩家过期状态

    Returns:
        (是否可攻击, 原因说明)
    """
    # 不能攻击自己
    if attacker.id == defender.id:
        return False, "不能攻击自己的庄园"

    # 检查进攻方保护状态
    if attacker.is_under_newbie_protection:
        return False, "新手保护期内无法发起攻击"
    if attacker.is_under_peace_shield:
        return False, "免战牌保护期内无法发起攻击"

    target_block_reason = get_target_attack_block_reason(
        defender,
        recent_attacks=recent_attacks,
        now=now,
        use_cached_recent_attacks=use_cached_recent_attacks,
        check_defeat_protection=check_defeat_protection,
        check_stale_bot=check_stale_bot,
    )
    if target_block_reason:
        return False, target_block_reason

    # 检查声望差值保护
    color = get_prestige_color(attacker.prestige, defender.prestige)
    if color == "green":
        return False, "对方声望过低，无法攻击"
    if color == "red":
        return False, "对方声望过高，无法攻击"

    return True, ""


def get_target_attack_block_reason(
    defender: Manor,
    *,
    recent_attacks: Optional[int] = None,
    now: Optional[datetime] = None,
    use_cached_recent_attacks: bool = True,
    check_defeat_protection: bool = True,
    check_stale_bot: bool = True,
) -> str:
    """Return a blocker shared by every potential attacker for this target."""
    if (
        check_stale_bot
        and BotProfile.objects.filter(
            manor_id=defender.id,
            state=BotProfile.State.STALE,
        ).exists()
    ):
        return "该虚拟玩家暂不可用"
    if defender.is_under_newbie_protection:
        return "对方处于新手保护期"
    if check_defeat_protection and defender.is_under_defeat_protection:
        return "对方处于战败保护期"
    if defender.is_under_peace_shield:
        return "对方处于免战牌保护期"

    # 检查目标24小时内被攻击次数（防止小号集群攻击）
    now = now or timezone.now()
    if recent_attacks is None:
        recent_attacks = get_recent_attacks_24h(defender, now=now, use_cache=use_cached_recent_attacks)
    if recent_attacks >= PVPConstants.RAID_MAX_DAILY_ATTACKS_RECEIVED:
        return "该目标今日已被多次攻击，暂时无法攻击"

    return ""


def is_target_globally_attackable(
    defender: Manor,
    *,
    recent_attacks: Optional[int] = None,
    now: Optional[datetime] = None,
) -> bool:
    """Return whether target-side state permits attacks from some eligible player."""
    return not get_target_attack_block_reason(defender, recent_attacks=recent_attacks, now=now)


def get_asset_level(manor: Manor) -> Tuple[str, int]:
    """
    计算庄园的资产等级。

    Args:
        manor: 庄园对象

    Returns:
        (资产等级名称, 总资产值)
    """
    # 计算总资产 = 粮食 + 银两 + 仓库物品总价值
    total_assets = manor.grain + manor.silver

    # 使用聚合查询计算仓库物品总价值（优化：避免循环查询）
    item_value = (
        InventoryItem.objects.filter(manor=manor, storage_location=InventoryItem.StorageLocation.WAREHOUSE).aggregate(
            total=Sum(F("quantity") * F("template__price"))
        )["total"]
        or 0
    )

    total_assets += item_value

    # 判断资产等级
    if total_assets < PVPConstants.ASSET_LEVEL_POOR:
        return "匮乏", total_assets
    elif total_assets < PVPConstants.ASSET_LEVEL_NORMAL:
        return "一般", total_assets
    elif total_assets < PVPConstants.ASSET_LEVEL_RICH:
        return "充裕", total_assets
    else:
        return "富足", total_assets


def get_troop_description(total_count: int) -> str:
    """
    根据护院总数返回模糊描述。

    Args:
        total_count: 护院总数

    Returns:
        模糊描述文字
    """
    if total_count < 100:
        return "少量"
    elif total_count < 500:
        return "旗鼓相当"
    elif total_count < 2000:
        return "较多"
    else:
        return "庞大"
