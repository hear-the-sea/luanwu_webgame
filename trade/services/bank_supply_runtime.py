from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable

from django.core.cache import cache
from django.db.models import Sum
from django.utils import timezone

from core.utils import safe_int
from core.utils.cache_lock import release_cache_key_if_owner
from core.utils.infrastructure import CACHE_INFRASTRUCTURE_EXCEPTIONS, DATABASE_INFRASTRUCTURE_EXCEPTIONS
from gameplay.models import InventoryItem, Manor
from trade.models import GoldBarExchangeLog

from .bank_pricing import (
    ACTIVE_DAYS_THRESHOLD,
    GOLD_BAR_ITEM_KEY,
    GOLD_BAR_TARGET_SUPPLY,
    SUPPLY_CACHE_KEY,
    SUPPLY_CACHE_TTL,
    SUPPLY_STALE_CACHE_KEY,
    SUPPLY_STALE_CACHE_TTL,
    GoldBarPricingUnavailableError,
)
from .cache_resilience import (
    best_effort_cache_add,
    best_effort_cache_delete,
    best_effort_cache_get,
    best_effort_cache_set,
    strict_cache_add,
    strict_cache_get,
)

logger = logging.getLogger(__name__)


BANK_CACHE_INFRASTRUCTURE_EXCEPTIONS = CACHE_INFRASTRUCTURE_EXCEPTIONS
BANK_QUERY_INFRASTRUCTURE_EXCEPTIONS = DATABASE_INFRASTRUCTURE_EXCEPTIONS
BANK_CACHE_COMPONENT = "bank_cache"


@dataclass
class BankServiceHooks:
    safe_cache_get: Callable[[str, Any], Any] | None = None
    safe_cache_set: Callable[[str, Any, int], None] | None = None
    safe_cache_add: Callable[[str, Any, int], bool] | None = None
    safe_cache_delete: Callable[[str], None] | None = None
    release_cache_lock_if_owner: Callable[[str, str], None] | None = None
    get_today_exchange_count: Callable[[Manor], int] | None = None
    get_effective_gold_supply_override: Callable[[], Callable[..., int] | None] | None = None
    calculate_supply_factor_override: Callable[[], Callable[..., float] | None] | None = None


_hooks = BankServiceHooks()


def configure_bank_service_hooks(
    *,
    safe_cache_get: Callable[[str, Any], Any] | None = None,
    safe_cache_set: Callable[[str, Any, int], None] | None = None,
    safe_cache_add: Callable[[str, Any, int], bool] | None = None,
    safe_cache_delete: Callable[[str], None] | None = None,
    release_cache_lock_if_owner: Callable[[str, str], None] | None = None,
    get_today_exchange_count: Callable[[Manor], int] | None = None,
    get_effective_gold_supply_override: Callable[[], Callable[..., int] | None] | None = None,
    calculate_supply_factor_override: Callable[[], Callable[..., float] | None] | None = None,
) -> None:
    global _hooks
    _hooks = BankServiceHooks(
        safe_cache_get=safe_cache_get,
        safe_cache_set=safe_cache_set,
        safe_cache_add=safe_cache_add,
        safe_cache_delete=safe_cache_delete,
        release_cache_lock_if_owner=release_cache_lock_if_owner,
        get_today_exchange_count=get_today_exchange_count,
        get_effective_gold_supply_override=get_effective_gold_supply_override,
        calculate_supply_factor_override=calculate_supply_factor_override,
    )


def _default_safe_cache_get(key: str, default: Any = None) -> Any:
    return best_effort_cache_get(
        cache,
        key,
        default,
        logger=logger,
        component=BANK_CACHE_COMPONENT,
        infrastructure_exceptions=BANK_CACHE_INFRASTRUCTURE_EXCEPTIONS,
    )


def _default_safe_cache_set(key: str, value: Any, timeout: int) -> None:
    best_effort_cache_set(
        cache,
        key,
        value,
        timeout,
        logger=logger,
        component=BANK_CACHE_COMPONENT,
        infrastructure_exceptions=BANK_CACHE_INFRASTRUCTURE_EXCEPTIONS,
    )


def _default_safe_cache_add(key: str, value: Any, timeout: int) -> bool:
    return best_effort_cache_add(
        cache,
        key,
        value,
        timeout,
        logger=logger,
        component=BANK_CACHE_COMPONENT,
        infrastructure_exceptions=BANK_CACHE_INFRASTRUCTURE_EXCEPTIONS,
    )


def _default_safe_cache_delete(key: str) -> None:
    best_effort_cache_delete(
        cache,
        key,
        logger=logger,
        component=BANK_CACHE_COMPONENT,
        infrastructure_exceptions=BANK_CACHE_INFRASTRUCTURE_EXCEPTIONS,
    )


def strict_cache_get_value(key: str, default: Any = None) -> Any:
    return strict_cache_get(
        cache,
        key,
        default,
        logger=logger,
        component=BANK_CACHE_COMPONENT,
        infrastructure_exceptions=BANK_CACHE_INFRASTRUCTURE_EXCEPTIONS,
        unavailable_error_factory=GoldBarPricingUnavailableError,
    )


def strict_cache_add_value(key: str, value: Any, timeout: int) -> bool:
    return strict_cache_add(
        cache,
        key,
        value,
        timeout,
        logger=logger,
        component=BANK_CACHE_COMPONENT,
        infrastructure_exceptions=BANK_CACHE_INFRASTRUCTURE_EXCEPTIONS,
        unavailable_error_factory=GoldBarPricingUnavailableError,
    )


def safe_cache_get_value(key: str, default: Any = None) -> Any:
    if _hooks.safe_cache_get is not None:
        return _hooks.safe_cache_get(key, default)
    return _default_safe_cache_get(key, default)


def safe_cache_set_value(key: str, value: Any, timeout: int) -> None:
    if _hooks.safe_cache_set is not None:
        _hooks.safe_cache_set(key, value, timeout)
        return
    _default_safe_cache_set(key, value, timeout)


def safe_cache_add_value(key: str, value: Any, timeout: int) -> bool:
    if _hooks.safe_cache_add is not None:
        return _hooks.safe_cache_add(key, value, timeout)
    return _default_safe_cache_add(key, value, timeout)


def safe_cache_delete_value(key: str) -> None:
    if _hooks.safe_cache_delete is not None:
        _hooks.safe_cache_delete(key)
        return
    _default_safe_cache_delete(key)


def release_cache_lock_if_owner_value(lock_key: str, lock_token: str) -> None:
    if _hooks.release_cache_lock_if_owner is not None:
        _hooks.release_cache_lock_if_owner(lock_key, lock_token)
        return

    release_cache_key_if_owner(
        lock_key,
        lock_token=lock_token,
        logger=logger,
        log_context="gold supply cache lock release",
    )


def get_today_exchange_count_value(manor: Manor) -> int:
    if _hooks.get_today_exchange_count is not None:
        return _hooks.get_today_exchange_count(manor)

    today = timezone.now().date()
    count = GoldBarExchangeLog.objects.filter(manor=manor, exchange_date=today).aggregate(total=Sum("quantity"))[
        "total"
    ]
    return max(0, safe_int(count, 0))


def resolve_get_effective_gold_supply(default_fn: Callable[..., int]) -> Callable[..., int]:
    if _hooks.get_effective_gold_supply_override is not None:
        override = _hooks.get_effective_gold_supply_override()
        if override is not None:
            return override
    return default_fn


def resolve_calculate_supply_factor(default_fn: Callable[..., float]) -> Callable[..., float]:
    if _hooks.calculate_supply_factor_override is not None:
        override = _hooks.calculate_supply_factor_override()
        if override is not None:
            return override
    return default_fn


def normalize_supply_value(raw_value: Any, *, source: str, fail_closed: bool) -> int:
    try:
        return max(0, int(raw_value))
    except (TypeError, ValueError) as exc:
        logger.warning("Invalid gold supply value from %s: %r", source, raw_value, exc_info=True)
        if fail_closed:
            raise GoldBarPricingUnavailableError() from exc
        return GOLD_BAR_TARGET_SUPPLY


def get_effective_gold_supply_data(*, fail_closed: bool = False) -> tuple[int, str]:
    cache_get = strict_cache_get_value if fail_closed else safe_cache_get_value
    cache_add = strict_cache_add_value if fail_closed else safe_cache_add_value

    cached = cache_get(SUPPLY_CACHE_KEY)
    if cached is not None:
        return normalize_supply_value(cached, source="cache", fail_closed=fail_closed), "cache"

    lock_key = f"{SUPPLY_CACHE_KEY}:lock"
    lock_token = uuid.uuid4().hex
    lock_acquired = cache_add(lock_key, lock_token, timeout=10)

    if not lock_acquired:
        stale = cache_get(SUPPLY_STALE_CACHE_KEY)
        if stale is not None:
            stale_value = normalize_supply_value(stale, source="stale_cache", fail_closed=fail_closed)
            if fail_closed:
                logger.error("Gold supply strict path rejected stale cache pricing")
                raise GoldBarPricingUnavailableError()
            logger.info("Gold supply cache miss, using stale cache value: %d", stale_value)
            return stale_value, "stale_cache"
        if fail_closed:
            logger.error("Gold supply strict path rejected default pricing fallback")
            raise GoldBarPricingUnavailableError()
        logger.warning("Gold supply cache miss and no stale cache, using default")
        return GOLD_BAR_TARGET_SUPPLY, "default"

    try:
        cutoff = timezone.now() - timedelta(days=ACTIVE_DAYS_THRESHOLD)

        result = InventoryItem.objects.filter(
            template__key=GOLD_BAR_ITEM_KEY,
            manor__user__last_login__gte=cutoff,
        ).aggregate(total=Sum("quantity"))

        total = max(0, safe_int(result["total"], 0))
        safe_cache_set_value(SUPPLY_CACHE_KEY, total, SUPPLY_CACHE_TTL)
        safe_cache_set_value(SUPPLY_STALE_CACHE_KEY, total, SUPPLY_STALE_CACHE_TTL)
        return total, "db"
    except BANK_QUERY_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning(
            "Failed to query gold supply: %s",
            exc,
            exc_info=True,
            extra={"degraded": True, "component": "bank_gold_supply"},
        )
        if fail_closed:
            raise GoldBarPricingUnavailableError() from exc
        stale = safe_cache_get_value(SUPPLY_STALE_CACHE_KEY)
        if stale is not None:
            return normalize_supply_value(stale, source="stale_cache", fail_closed=False), "stale_cache"
        return GOLD_BAR_TARGET_SUPPLY, "default"
    finally:
        release_cache_lock_if_owner_value(lock_key, lock_token)
