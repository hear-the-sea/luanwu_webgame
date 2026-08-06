"""
缓存管理服务

提供统一的缓存key管理和缓存操作。
"""

from __future__ import annotations

import logging
from functools import wraps
from time import monotonic, sleep
from typing import Callable, TypeVar, cast

from django.core.cache import cache

from core.utils.cache_lock import acquire_best_effort_lock, release_best_effort_lock
from gameplay.services.utils.cache_exceptions import CACHE_INFRASTRUCTURE_EXCEPTIONS

logger = logging.getLogger(__name__)

# 缓存超时时间常量（秒）
CACHE_TIMEOUT_SHORT = 5  # 5秒 - 实时性要求高的数据
CACHE_TIMEOUT_MEDIUM = 30  # 30秒 - 中等实时性要求
CACHE_TIMEOUT_LONG = 60  # 60秒 - 低实时性要求
CACHE_TIMEOUT_RANKING = 60  # 排行榜缓存
CACHE_TIMEOUT_CONFIG = 300  # 5分钟 - 配置类数据
RECRUITMENT_HALL_CACHE_VERSION = 1  # 聚贤庄上下文缓存版本
CACHE_GET_OR_SET_LOCK_TIMEOUT_SECONDS = 5
CACHE_GET_OR_SET_LOCK_WAIT_SECONDS = 0.2
CACHE_GET_OR_SET_LOCK_POLL_INTERVAL_SECONDS = 0.01


class CacheKeys:
    """缓存键前缀和生成器"""

    # 排行榜相关
    RANKING_PRESTIGE = "ranking:prestige"
    RANKING_TOTAL_PLAYERS = "ranking:total_players"
    PLAYER_RANK_PREFIX = "player_rank"

    # 消息相关
    UNREAD_COUNT_PREFIX = "unread_count:manor"

    # 庄园状态
    MANOR_STATS_PREFIX = "manor_stats"

    # 交易行统计
    MARKET_STATS = "market:stats"

    # 科技加成
    TECH_BONUS_PREFIX = "tech_bonus"
    HOME_HOURLY_RATES_PREFIX = "manor_home_hourly_rates"

    # 门客模板相关
    GUEST_TEMPLATES_BY_RARITY = "guest_templates:by_rarity"
    HERMIT_TEMPLATES = "guest_templates:hermit"
    RECRUITMENT_HALL_CONTEXT_PREFIX = "recruitment_hall:context"

    @staticmethod
    def player_rank(manor_id: int, prestige: int) -> str:
        """玩家排名缓存键"""
        return f"{CacheKeys.PLAYER_RANK_PREFIX}:{manor_id}:{prestige}"

    @staticmethod
    def unread_count(manor_id: int) -> str:
        """未读消息数缓存键"""
        return f"{CacheKeys.UNREAD_COUNT_PREFIX}:{manor_id}"

    @staticmethod
    def manor_stats(manor_id: int) -> str:
        """庄园统计数据缓存键"""
        return f"{CacheKeys.MANOR_STATS_PREFIX}:{manor_id}"

    @staticmethod
    def market_stats() -> str:
        """交易行全局统计缓存键。"""
        return CacheKeys.MARKET_STATS

    @staticmethod
    def tech_bonus(manor_id: int, tech_key: str) -> str:
        """科技加成缓存键"""
        return f"{CacheKeys.TECH_BONUS_PREFIX}:{manor_id}:{tech_key}"

    @staticmethod
    def home_hourly_rates(manor_id: int) -> str:
        """首页建筑产出缓存键"""
        return f"{CacheKeys.HOME_HOURLY_RATES_PREFIX}:{manor_id}"

    @staticmethod
    def recruitment_hall_context(manor_id: int, *, version: int | None = None) -> str:
        """聚贤庄页面上下文缓存键。"""
        base_key = f"{CacheKeys.RECRUITMENT_HALL_CONTEXT_PREFIX}:{manor_id}"
        if version is None:
            return base_key
        return f"{base_key}:v{int(version)}"


def recruitment_hall_context_cache_key(manor_id: int) -> str:
    """返回聚贤庄页面上下文的当前版本缓存键。"""
    return CacheKeys.recruitment_hall_context(manor_id, version=RECRUITMENT_HALL_CACHE_VERSION)


def invalidate_home_stats_cache(manor_id: int) -> None:
    """清除首页统计类缓存。"""
    try:
        cache.delete(CacheKeys.home_hourly_rates(manor_id))
    except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning("cache.delete failed in invalidate_home_stats_cache(): manor_id=%s error=%s", manor_id, exc)


def invalidate_market_stats_cache() -> None:
    """清除交易行全局统计缓存。"""
    try:
        cache.delete(CacheKeys.market_stats())
    except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning("cache.delete failed in invalidate_market_stats_cache(): error=%s", exc)


def invalidate_recruitment_hall_cache(manor_id: int) -> None:
    """清除聚贤庄页面上下文缓存。"""
    # 同时清理历史无版本键，兼容旧逻辑残留。
    try:
        cache.delete_many(
            [
                CacheKeys.recruitment_hall_context(manor_id),
                recruitment_hall_context_cache_key(manor_id),
            ]
        )
    except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning(
            "cache.delete_many failed in invalidate_recruitment_hall_cache(): manor_id=%s error=%s",
            manor_id,
            exc,
        )


def invalidate_manor_cache(manor_id: int) -> None:
    """
    清除庄园相关的所有缓存。

    在庄园数据发生变化时调用。

    Args:
        manor_id: 庄园ID
    """
    keys_to_delete = [
        CacheKeys.unread_count(manor_id),
        CacheKeys.manor_stats(manor_id),
    ]
    try:
        cache.delete_many(keys_to_delete)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning("cache.delete_many failed in invalidate_manor_cache(): manor_id=%s error=%s", manor_id, exc)


def invalidate_ranking_cache() -> None:
    """
    清除排行榜缓存。

    在声望发生变化时调用。
    """
    try:
        cache.delete(CacheKeys.RANKING_PRESTIGE)
        cache.delete(CacheKeys.RANKING_TOTAL_PLAYERS)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning("cache.delete failed in invalidate_ranking_cache(): error=%s", exc)


T = TypeVar("T")


def cached(
    key_func: Callable[..., str],
    timeout: int = CACHE_TIMEOUT_MEDIUM,
    cache_none: bool = False,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    缓存装饰器。

    Args:
        key_func: 生成缓存键的函数，接收与被装饰函数相同的参数
        timeout: 缓存超时时间（秒）
        cache_none: 是否缓存None值

    Returns:
        装饰器函数

    Example:
        @cached(lambda manor: f"manor_data:{manor.id}", timeout=60)
        def get_manor_data(manor):
            return expensive_computation(manor)
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            cache_key = key_func(*args, **kwargs)
            sentinel = object()
            try:
                result = cache.get(cache_key, sentinel)
            except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
                logger.warning("cache.get failed in cached(): key=%s error=%s", cache_key, exc, exc_info=True)
                result = sentinel

            if result is not sentinel:
                # If `cache_none=False` we don't normally store None, but keep
                # compatibility with legacy callers that may have cached None.
                if result is None and not cache_none:
                    return cast(T, None)
                return cast(T, result)

            computed = func(*args, **kwargs)
            if computed is not None or cache_none:
                try:
                    cache.set(cache_key, computed, timeout=timeout)
                except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
                    logger.warning("cache.set failed in cached(): key=%s error=%s", cache_key, exc, exc_info=True)
            return cast(T, computed)

        return wrapper

    return decorator


def get_or_set(
    key: str,
    default_func: Callable[[], T],
    timeout: int = CACHE_TIMEOUT_MEDIUM,
    *,
    lock_timeout: int | None = CACHE_GET_OR_SET_LOCK_TIMEOUT_SECONDS,
    lock_wait_timeout: float = CACHE_GET_OR_SET_LOCK_WAIT_SECONDS,
) -> T:
    """
    获取缓存值，如果不存在则计算并设置。

    缓存 miss 时使用短生命周期的令牌锁，并在锁竞争期间短暂等待后再次读取，
    避免热点 key 在过期瞬间被多个请求同时回源。传入 ``lock_timeout=None``
    可为特殊场景关闭防击穿锁。

    Args:
        key: 缓存键
        default_func: 计算默认值的函数
        timeout: 缓存超时时间
        lock_timeout: 防击穿锁超时时间；``None`` 表示关闭
        lock_wait_timeout: 未抢到锁时等待其他请求写入缓存的最长时间

    Returns:
        缓存值或计算的默认值
    """

    def _read_cache(*, log_failure: bool = True) -> object:
        try:
            return cache.get(key)
        except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
            if log_failure:
                logger.warning("cache.get failed in get_or_set(): key=%s error=%s", key, exc, exc_info=True)
            return None

    def _compute_and_store() -> T:
        computed = default_func()
        try:
            cache.set(key, computed, timeout=timeout)
        except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
            logger.warning("cache.set failed in get_or_set(): key=%s error=%s", key, exc, exc_info=True)
        return cast(T, computed)

    result = _read_cache()
    if result is not None:
        return cast(T, result)
    if lock_timeout is None:
        return _compute_and_store()

    lock_key = f"{key}:get_or_set:lock"
    acquired, from_cache, lock_token = acquire_best_effort_lock(
        lock_key,
        timeout_seconds=max(1, int(lock_timeout)),
        logger=logger,
        log_context="get_or_set cache stampede protection",
    )
    if acquired:
        try:
            # The value may have been populated between the first read and lock acquisition.
            result = _read_cache()
            if result is not None:
                return cast(T, result)
            return _compute_and_store()
        finally:
            release_best_effort_lock(
                lock_key,
                from_cache=from_cache,
                lock_token=lock_token,
                logger=logger,
                log_context="get_or_set cache stampede protection release",
            )

    deadline = monotonic() + max(0.0, float(lock_wait_timeout))
    while monotonic() < deadline:
        sleep(min(CACHE_GET_OR_SET_LOCK_POLL_INTERVAL_SECONDS, max(0.0, deadline - monotonic())))
        result = _read_cache(log_failure=False)
        if result is not None:
            return cast(T, result)

    # A slow or failed producer must not make the read path fail forever. The caller
    # computes locally after the bounded wait, while the lock owner remains protected.
    return _compute_and_store()
