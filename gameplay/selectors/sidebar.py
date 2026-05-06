from __future__ import annotations

import logging

from django.core.cache import cache

from core.utils import safe_non_negative_int
from gameplay.services.utils.cache_exceptions import CACHE_INFRASTRUCTURE_EXCEPTIONS

logger = logging.getLogger(__name__)

SIDEBAR_RANK_CACHE_TIMEOUT = 30


def _safe_cache_get(key: str, default=None):
    try:
        return cache.get(key, default)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        logger.warning("Failed to read cache key: %s", key, exc_info=True)
        return default


def _safe_cache_set(key: str, value, timeout: int) -> None:
    try:
        cache.set(key, value, timeout=timeout)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS:
        logger.warning("Failed to write cache key: %s", key, exc_info=True)


def load_sidebar_rank(manor) -> int:
    from gameplay.services.ranking import get_player_rank

    cache_key_rank = f"sidebar:rank:{manor.id}"
    cached_rank = _safe_cache_get(cache_key_rank)
    if cached_rank is not None:
        return safe_non_negative_int(cached_rank)

    rank = safe_non_negative_int(get_player_rank(manor))
    _safe_cache_set(cache_key_rank, rank, timeout=SIDEBAR_RANK_CACHE_TIMEOUT)
    return rank
