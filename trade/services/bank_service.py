"""
钱庄服务

动态汇率机制：
  实时汇率 = 基准价 × 总量系数 × 累进系数

  - 总量系数：基于活跃玩家的金条总量，金条越多价格越高（通胀控制）
  - 累进系数：基于当日个人已兑换数量，越买越贵（软限制）
"""

import logging
from typing import Any, Callable

from django.core.cache import cache
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.utils import safe_int
from core.utils.cache_lock import release_cache_key_if_owner
from core.utils.infrastructure import CACHE_INFRASTRUCTURE_EXCEPTIONS
from gameplay.models import Manor, ResourceEvent
from gameplay.services.inventory.core import add_item_to_inventory_locked
from gameplay.services.resources import spend_resources_locked
from trade.models import GoldBarExchangeLog

from . import rate_calculations as _rate_calculations
from .bank_flows import build_bank_info_payload
from .bank_flows import build_exchange_result as build_exchange_result_payload
from .bank_flows import execute_gold_bar_exchange, resolve_pricing_status

# Pure pricing constants and math functions extracted to bank_pricing.
from .bank_pricing import (  # noqa: F401
    ACTIVE_DAYS_THRESHOLD,
    DEGRADED_PRICING_SOURCES,
    GOLD_BAR_BASE_PRICE,
    GOLD_BAR_FEE_RATE,
    GOLD_BAR_ITEM_KEY,
    GOLD_BAR_MAX_PRICE,
    GOLD_BAR_MIN_PRICE,
    GOLD_BAR_PROGRESSIVE_FACTOR,
    GOLD_BAR_SUPPLY_FACTOR,
    GOLD_BAR_TARGET_SUPPLY,
    SUPPLY_CACHE_KEY,
    SUPPLY_CACHE_TTL,
    SUPPLY_STALE_CACHE_KEY,
    SUPPLY_STALE_CACHE_TTL,
    GoldBarPricingUnavailableError,
    _calculate_supply_factor_from_supply,
    _normalize_positive_quantity,
    calculate_progressive_factor,
)
from .bank_runtime import build_exchange_result as runtime_build_exchange_result
from .bank_runtime import configure_rate_calculation_hooks
from .bank_runtime import get_today_exchange_count as runtime_get_today_exchange_count
from .bank_runtime import grant_gold_bars_locked as runtime_grant_gold_bars_locked
from .bank_runtime import record_gold_bar_exchange_locked as runtime_record_gold_bar_exchange_locked
from .bank_runtime import release_cache_lock_if_owner_entry
from .bank_runtime import safe_cache_add as runtime_safe_cache_add
from .bank_runtime import safe_cache_delete as runtime_safe_cache_delete
from .bank_runtime import safe_cache_get as runtime_safe_cache_get
from .bank_runtime import safe_cache_set as runtime_safe_cache_set
from .bank_runtime import spend_exchange_cost_locked as runtime_spend_exchange_cost_locked
from .bank_runtime import strict_cache_add_entry, strict_cache_get_entry

logger = logging.getLogger(__name__)


BANK_INFRASTRUCTURE_EXCEPTIONS = CACHE_INFRASTRUCTURE_EXCEPTIONS
BANK_CACHE_COMPONENT = "bank_cache"


def _safe_cache_get(key: str, default: Any = None) -> Any:
    return runtime_safe_cache_get(
        key,
        default,
        cache_backend=cache,
        logger=logger,
        component=BANK_CACHE_COMPONENT,
        infrastructure_exceptions=BANK_INFRASTRUCTURE_EXCEPTIONS,
    )


def _safe_cache_set(key: str, value: Any, timeout: int) -> None:
    runtime_safe_cache_set(
        key,
        value,
        timeout,
        cache_backend=cache,
        logger=logger,
        component=BANK_CACHE_COMPONENT,
        infrastructure_exceptions=BANK_INFRASTRUCTURE_EXCEPTIONS,
    )


def _safe_cache_add(key: str, value: Any, timeout: int) -> bool:
    return runtime_safe_cache_add(
        key,
        value,
        timeout,
        cache_backend=cache,
        logger=logger,
        component=BANK_CACHE_COMPONENT,
        infrastructure_exceptions=BANK_INFRASTRUCTURE_EXCEPTIONS,
    )


def _safe_cache_delete(key: str) -> None:
    runtime_safe_cache_delete(
        key,
        cache_backend=cache,
        logger=logger,
        component=BANK_CACHE_COMPONENT,
        infrastructure_exceptions=BANK_INFRASTRUCTURE_EXCEPTIONS,
    )


def _strict_cache_get(key: str, default: Any = None) -> Any:
    return strict_cache_get_entry(
        key,
        default,
        cache_backend=cache,
        logger=logger,
        component=BANK_CACHE_COMPONENT,
        infrastructure_exceptions=BANK_INFRASTRUCTURE_EXCEPTIONS,
        unavailable_error_factory=GoldBarPricingUnavailableError,
    )


def _strict_cache_add(key: str, value: Any, timeout: int) -> bool:
    return strict_cache_add_entry(
        key,
        value,
        timeout,
        cache_backend=cache,
        logger=logger,
        component=BANK_CACHE_COMPONENT,
        infrastructure_exceptions=BANK_INFRASTRUCTURE_EXCEPTIONS,
        unavailable_error_factory=GoldBarPricingUnavailableError,
    )


def _release_cache_lock_if_owner(lock_key: str, lock_token: str) -> None:
    release_cache_lock_if_owner_entry(
        lock_key,
        lock_token,
        release_cache_key_if_owner=release_cache_key_if_owner,
        logger=logger,
        log_context="gold supply cache lock release",
        safe_cache_get=_safe_cache_get,
        safe_cache_delete=_safe_cache_delete,
    )


def get_today_exchange_count(manor: Manor) -> int:
    """获取今日已兑换金条数量"""
    return runtime_get_today_exchange_count(
        manor,
        aggregate_quantity=lambda **filters: GoldBarExchangeLog.objects.filter(**filters).aggregate(
            total=Sum("quantity")
        ),
        now_func=timezone.now,
        safe_int=safe_int,
    )


# ============ 动态汇率计算 ============
from .rate_calculations import (  # noqa: E402,F401
    _get_effective_gold_supply_data,
    _normalize_supply_value,
    calculate_dynamic_rate,
    calculate_gold_bar_cost,
    calculate_next_rate,
    calculate_supply_factor,
    get_effective_gold_supply,
)

_IMPORTED_GET_EFFECTIVE_GOLD_SUPPLY = get_effective_gold_supply
_IMPORTED_CALCULATE_SUPPLY_FACTOR = calculate_supply_factor


def _get_effective_gold_supply_override() -> Callable[..., Any] | None:
    if get_effective_gold_supply is _IMPORTED_GET_EFFECTIVE_GOLD_SUPPLY:
        return None
    return get_effective_gold_supply


def _calculate_supply_factor_override() -> Callable[..., Any] | None:
    if calculate_supply_factor is _IMPORTED_CALCULATE_SUPPLY_FACTOR:
        return None
    return calculate_supply_factor


def _hook_safe_cache_get(key: str, default: Any = None) -> Any:
    return _safe_cache_get(key, default)


def _hook_safe_cache_set(key: str, value: Any, timeout: int) -> None:
    _safe_cache_set(key, value, timeout)


def _hook_safe_cache_add(key: str, value: Any, timeout: int) -> bool:
    return _safe_cache_add(key, value, timeout)


def _hook_safe_cache_delete(key: str) -> None:
    _safe_cache_delete(key)


def _hook_release_cache_lock_if_owner(lock_key: str, lock_token: str) -> None:
    _release_cache_lock_if_owner(lock_key, lock_token)


def _hook_get_today_exchange_count(manor: Manor) -> int:
    return get_today_exchange_count(manor)


configure_rate_calculation_hooks(
    _rate_calculations,
    safe_cache_get=_hook_safe_cache_get,
    safe_cache_set=_hook_safe_cache_set,
    safe_cache_add=_hook_safe_cache_add,
    safe_cache_delete=_hook_safe_cache_delete,
    release_cache_lock_if_owner=_hook_release_cache_lock_if_owner,
    get_today_exchange_count=_hook_get_today_exchange_count,
    get_effective_gold_supply_override=_get_effective_gold_supply_override,
    calculate_supply_factor_override=_calculate_supply_factor_override,
)


def _grant_gold_bars_locked(manor: Manor, quantity: int) -> None:
    runtime_grant_gold_bars_locked(
        manor,
        quantity,
        add_item_to_inventory_locked=add_item_to_inventory_locked,
        gold_bar_item_key=GOLD_BAR_ITEM_KEY,
    )


def _spend_exchange_cost_locked(manor: Manor, quantity: int, cost_info: dict[str, Any]) -> int:
    return runtime_spend_exchange_cost_locked(
        manor,
        quantity,
        cost_info,
        spend_resources_locked=spend_resources_locked,
        bank_exchange_reason=ResourceEvent.Reason.BANK_EXCHANGE,
    )


def _record_gold_bar_exchange_locked(manor: Manor, quantity: int, total_cost: int) -> None:
    runtime_record_gold_bar_exchange_locked(
        manor,
        quantity,
        total_cost,
        gold_bar_exchange_log_model=GoldBarExchangeLog,
    )


def _build_exchange_result(manor: Manor, quantity: int, cost_info: dict[str, Any]) -> dict[str, Any]:
    return runtime_build_exchange_result(
        manor,
        quantity,
        cost_info,
        build_exchange_result_payload=build_exchange_result_payload,
        calculate_next_rate=calculate_next_rate,
    )


def _resolve_pricing_status(pricing_source: str) -> tuple[bool, str]:
    return resolve_pricing_status(pricing_source)


def exchange_gold_bar(manor: Manor, quantity: int) -> dict:
    """
    兑换金条（动态汇率版本）

    安全修复：
    - 将价格计算移入 transaction.atomic() 内部
    - 依托 Manor 行锁实现用户级串行化，防止并发低价买入漏洞

    Args:
        manor: 庄园对象
        quantity: 兑换数量

    Returns:
        dict: 兑换结果信息

    Raises:
        ValueError: 参数错误、银两不足等
    """
    return execute_gold_bar_exchange(
        manor,
        quantity,
        transaction_atomic=transaction.atomic,
        manor_model=Manor,
        normalize_positive_quantity=_normalize_positive_quantity,
        calculate_gold_bar_cost=calculate_gold_bar_cost,
        spend_exchange_cost_locked=_spend_exchange_cost_locked,
        grant_gold_bars_locked=_grant_gold_bars_locked,
        record_gold_bar_exchange_locked=_record_gold_bar_exchange_locked,
        clear_supply_cache=lambda: _safe_cache_delete(SUPPLY_CACHE_KEY),
        build_exchange_result=_build_exchange_result,
    )


def get_bank_info(manor: Manor) -> dict:
    """
    获取钱庄信息（动态汇率版本）

    Returns:
        dict: 包含动态汇率、手续费率、今日兑换情况等信息
    """
    return build_bank_info_payload(
        manor,
        get_effective_gold_supply_data=_get_effective_gold_supply_data,
        resolve_pricing_status=_resolve_pricing_status,
        get_today_exchange_count=get_today_exchange_count,
        calculate_supply_factor_from_supply=_calculate_supply_factor_from_supply,
        calculate_dynamic_rate=calculate_dynamic_rate,
        calculate_next_rate=calculate_next_rate,
        calculate_progressive_factor=calculate_progressive_factor,
        calculate_gold_bar_cost=calculate_gold_bar_cost,
        gold_bar_base_price=GOLD_BAR_BASE_PRICE,
        gold_bar_fee_rate=GOLD_BAR_FEE_RATE,
        gold_bar_min_price=GOLD_BAR_MIN_PRICE,
        gold_bar_max_price=GOLD_BAR_MAX_PRICE,
    )
