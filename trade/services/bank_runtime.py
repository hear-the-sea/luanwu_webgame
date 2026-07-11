from __future__ import annotations

from typing import Any, Callable

from core.exceptions import InsufficientResourceError, ItemNotFoundError, TradeValidationError

from .cache_resilience import (
    best_effort_cache_add,
    best_effort_cache_delete,
    best_effort_cache_get,
    best_effort_cache_set,
    strict_cache_add,
    strict_cache_get,
)


def configure_rate_calculation_hooks(
    rate_calculations_module: Any,
    *,
    safe_cache_get: Callable[[str, Any], Any],
    safe_cache_set: Callable[[str, Any, int], None],
    safe_cache_add: Callable[[str, Any, int], bool],
    safe_cache_delete: Callable[[str], None],
    release_cache_lock_if_owner: Callable[[str, str], None],
    get_today_exchange_count: Callable[[Any], int],
    get_effective_gold_supply_override: Callable[[], Callable[..., Any] | None],
    calculate_supply_factor_override: Callable[[], Callable[..., Any] | None],
) -> None:
    rate_calculations_module.configure_bank_service_hooks(
        safe_cache_get=safe_cache_get,
        safe_cache_set=safe_cache_set,
        safe_cache_add=safe_cache_add,
        safe_cache_delete=safe_cache_delete,
        release_cache_lock_if_owner=release_cache_lock_if_owner,
        get_today_exchange_count=get_today_exchange_count,
        get_effective_gold_supply_override=get_effective_gold_supply_override,
        calculate_supply_factor_override=calculate_supply_factor_override,
    )


def get_today_exchange_count(
    manor: Any,
    *,
    aggregate_quantity: Callable[..., Any],
    now_func: Callable[[], Any],
    safe_int: Callable[..., int],
) -> int:
    today = now_func().date()
    count = aggregate_quantity(manor=manor, exchange_date=today)["total"]
    return max(0, safe_int(count, 0))


def safe_cache_get(
    key: str,
    default: Any = None,
    *,
    cache_backend: Any,
    logger: Any,
    component: str,
    infrastructure_exceptions: tuple[type[Exception], ...],
) -> Any:
    return best_effort_cache_get(
        cache_backend,
        key,
        default,
        logger=logger,
        component=component,
        infrastructure_exceptions=infrastructure_exceptions,
    )


def safe_cache_set(
    key: str,
    value: Any,
    timeout: int,
    *,
    cache_backend: Any,
    logger: Any,
    component: str,
    infrastructure_exceptions: tuple[type[Exception], ...],
) -> None:
    best_effort_cache_set(
        cache_backend,
        key,
        value,
        timeout,
        logger=logger,
        component=component,
        infrastructure_exceptions=infrastructure_exceptions,
    )


def safe_cache_add(
    key: str,
    value: Any,
    timeout: int,
    *,
    cache_backend: Any,
    logger: Any,
    component: str,
    infrastructure_exceptions: tuple[type[Exception], ...],
) -> bool:
    return best_effort_cache_add(
        cache_backend,
        key,
        value,
        timeout,
        logger=logger,
        component=component,
        infrastructure_exceptions=infrastructure_exceptions,
    )


def safe_cache_delete(
    key: str,
    *,
    cache_backend: Any,
    logger: Any,
    component: str,
    infrastructure_exceptions: tuple[type[Exception], ...],
) -> None:
    best_effort_cache_delete(
        cache_backend,
        key,
        logger=logger,
        component=component,
        infrastructure_exceptions=infrastructure_exceptions,
    )


def strict_cache_get_entry(
    key: str,
    default: Any = None,
    *,
    cache_backend: Any,
    logger: Any,
    component: str,
    infrastructure_exceptions: tuple[type[Exception], ...],
    unavailable_error_factory: Callable[[], Exception],
) -> Any:
    return strict_cache_get(
        cache_backend,
        key,
        default,
        logger=logger,
        component=component,
        infrastructure_exceptions=infrastructure_exceptions,
        unavailable_error_factory=unavailable_error_factory,
    )


def strict_cache_add_entry(
    key: str,
    value: Any,
    timeout: int,
    *,
    cache_backend: Any,
    logger: Any,
    component: str,
    infrastructure_exceptions: tuple[type[Exception], ...],
    unavailable_error_factory: Callable[[], Exception],
) -> bool:
    return strict_cache_add(
        cache_backend,
        key,
        value,
        timeout,
        logger=logger,
        component=component,
        infrastructure_exceptions=infrastructure_exceptions,
        unavailable_error_factory=unavailable_error_factory,
    )


def release_cache_lock_if_owner_entry(
    lock_key: str,
    lock_token: str,
    *,
    release_cache_key_if_owner: Callable[..., bool],
    logger: Any,
    log_context: str,
) -> None:
    release_cache_key_if_owner(
        lock_key,
        lock_token=lock_token,
        logger=logger,
        log_context=log_context,
    )


def grant_gold_bars_locked(
    manor: Any,
    quantity: int,
    *,
    add_item_to_inventory_locked: Callable[..., Any],
    gold_bar_item_key: str,
) -> None:
    try:
        add_item_to_inventory_locked(manor, gold_bar_item_key, quantity)
    except ItemNotFoundError as exc:
        raise TradeValidationError("金条物品不存在，请联系管理员") from exc


def spend_exchange_cost_locked(
    manor: Any,
    quantity: int,
    cost_info: dict[str, Any],
    *,
    spend_resources_locked: Callable[..., Any],
    bank_exchange_reason: Any,
) -> int:
    total_cost = int(cost_info["total_cost"])
    try:
        spend_resources_locked(
            manor,
            {"silver": total_cost},
            note=f"兑换金条 x{quantity}",
            reason=bank_exchange_reason,
        )
    except InsufficientResourceError as exc:
        raise TradeValidationError(
            f"银两不足，需要 {total_cost:,} 银两" f"（基础 {cost_info['base_cost']:,} + 手续费 {cost_info['fee']:,}）"
        ) from exc
    return total_cost


def record_gold_bar_exchange_locked(
    manor: Any,
    quantity: int,
    total_cost: int,
    *,
    gold_bar_exchange_log_model: Any,
) -> None:
    gold_bar_exchange_log_model.objects.create(manor=manor, quantity=quantity, silver_cost=total_cost)


def build_exchange_result(
    manor: Any,
    quantity: int,
    cost_info: dict[str, Any],
    *,
    build_exchange_result_payload: Callable[..., dict[str, Any]],
    calculate_next_rate: Callable[..., int],
) -> dict[str, Any]:
    return build_exchange_result_payload(manor, quantity, cost_info, calculate_next_rate=calculate_next_rate)
