"""
排行榜服务模块

提供庄园声望排名功能。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from django.core.cache import cache
from django.db.models import Q

from ..models import Manor
from .utils.cache import CACHE_TIMEOUT_MEDIUM, CACHE_TIMEOUT_RANKING, CacheKeys, get_or_set
from .utils.cache_exceptions import CACHE_INFRASTRUCTURE_EXCEPTIONS

logger = logging.getLogger(__name__)


def _safe_cache_get(key: str):
    try:
        return cache.get(key)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning("Ranking cache.get failed: key=%s error=%s", key, exc, exc_info=True)
        return None


def _safe_cache_set(key: str, value: int, timeout: int) -> None:
    try:
        cache.set(key, value, timeout=timeout)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning("Ranking cache.set failed: key=%s error=%s", key, exc, exc_info=True)


def get_prestige_ranking(limit: int = 50) -> List[Dict[str, Any]]:
    """
    获取声望排行榜（带缓存）。

    Args:
        limit: 返回的排名数量，默认50

    Returns:
        排行榜列表，每项包含：
        - rank: 排名
        - manor_id: 庄园ID
        - manor_name: 庄园名称
        - username: 用户名
        - prestige: 声望值
    """
    cache_key = f"{CacheKeys.RANKING_PRESTIGE}:{limit}"

    def compute_ranking():
        manors = (
            Manor.objects.select_related("user")
            .filter(prestige__gt=0, bot_profile__isnull=True)
            .order_by("-prestige", "created_at")[:limit]
        )

        ranking = []
        for idx, manor in enumerate(manors, start=1):
            ranking.append(
                {
                    "rank": idx,
                    "manor_id": manor.id,
                    "manor_name": manor.display_name,
                    "username": manor.user.username,
                    "prestige": manor.prestige,
                }
            )
        return ranking

    return get_or_set(cache_key, compute_ranking, timeout=CACHE_TIMEOUT_RANKING)


def get_player_rank(manor: Manor) -> Optional[int]:
    """
    获取玩家的声望排名（使用30秒缓存优化）。

    Args:
        manor: 庄园实例

    Returns:
        排名（从1开始），如果没有声望则返回None
    """
    if manor.prestige <= 0 or hasattr(manor, "bot_profile"):
        return None

    # 使用缓存减少数据库查询
    cache_key = CacheKeys.player_rank(manor.id, manor.prestige)
    cached_rank = _safe_cache_get(cache_key)
    if cached_rank is not None:
        return cached_rank

    # Performance: compute rank using a single COUNT query.
    # Rank = 1 + count(prestige higher OR same prestige but created earlier)
    ahead_count = (
        Manor.objects.filter(
            bot_profile__isnull=True,
        )
        .filter(Q(prestige__gt=manor.prestige) | Q(prestige=manor.prestige, created_at__lt=manor.created_at))
        .count()
    )

    rank = ahead_count + 1
    # 缓存30秒，声望变化时 cache_key 会变化自动失效
    _safe_cache_set(cache_key, rank, timeout=CACHE_TIMEOUT_MEDIUM)
    return rank


def get_ranking_with_player_context(manor: Manor, limit: int = 50) -> Dict[str, Any]:
    """
    获取排行榜以及当前玩家的排名信息。

    Args:
        manor: 当前玩家的庄园
        limit: 排行榜显示数量

    Returns:
        {
            "ranking": 排行榜列表,
            "player_rank": 玩家排名（如果不在榜内）,
            "player_in_ranking": 玩家是否在排行榜内,
            "total_players": 有声望的玩家总数,
        }
    """
    ranking = get_prestige_ranking(limit)
    player_rank = get_player_rank(manor)

    # 缓存有声望玩家总数（60秒）
    def compute_total():
        return Manor.objects.filter(prestige__gt=0, bot_profile__isnull=True).count()

    total_players = get_or_set(CacheKeys.RANKING_TOTAL_PLAYERS, compute_total, timeout=CACHE_TIMEOUT_RANKING)

    # 检查玩家是否在排行榜内
    player_in_ranking = any(r["manor_id"] == manor.id for r in ranking)

    return {
        "ranking": ranking,
        "player_rank": player_rank,
        "player_in_ranking": player_in_ranking,
        "total_players": total_players,
    }
