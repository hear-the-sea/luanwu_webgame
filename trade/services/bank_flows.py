from __future__ import annotations

import logging

from django.db import ProgrammingError, transaction

from core.utils.infrastructure import (
    DATABASE_INFRASTRUCTURE_EXCEPTIONS,
    InfrastructureExceptions,
    combine_infrastructure_exceptions,
)

from .bank_pricing import GoldBarPricingUnavailableError

logger = logging.getLogger(__name__)
BANK_RATE_DISPLAY_EXCEPTIONS: InfrastructureExceptions = (ProgrammingError,) + combine_infrastructure_exceptions(
    GoldBarPricingUnavailableError,
    infrastructure_exceptions=DATABASE_INFRASTRUCTURE_EXCEPTIONS,
)


def execute_gold_bar_exchange(
    manor,
    quantity: int,
    *,
    transaction_atomic,
    manor_model,
    normalize_positive_quantity,
    calculate_gold_bar_cost,
    spend_exchange_cost_locked,
    grant_gold_bars_locked,
    record_gold_bar_exchange_locked,
    clear_supply_cache,
    build_exchange_result,
):
    quantity = normalize_positive_quantity(quantity)

    with transaction_atomic():
        manor_locked = manor_model.objects.select_for_update().get(pk=manor.pk)
        cost_info = calculate_gold_bar_cost(manor_locked, quantity, fail_closed=True)
        total_cost = spend_exchange_cost_locked(manor_locked, quantity, cost_info)
        grant_gold_bars_locked(manor_locked, quantity)
        record_gold_bar_exchange_locked(manor_locked, quantity, total_cost)

    clear_supply_cache()
    return build_exchange_result(manor, quantity, cost_info)


def resolve_pricing_status(pricing_source: str) -> tuple[bool, str]:
    if pricing_source == "stale_cache":
        return True, "钱庄汇率数据正在降级展示，当前价格可能不是最新值，已暂时关闭兑换。"
    if pricing_source == "default":
        return True, "钱庄汇率数据暂时不可用，已暂时关闭兑换。"
    return False, ""


def build_exchange_result(manor, quantity: int, cost_info: dict, *, calculate_next_rate) -> dict[str, object]:
    # 兑换已经完成，展示汇率不可用不能把成功扣费误报为交易失败。
    try:
        # 隔离可选查询的数据库错误，兼容调用方仍处于外层事务的场景。
        with transaction.atomic():
            next_rate = calculate_next_rate(manor, fail_closed=True)
    except BANK_RATE_DISPLAY_EXCEPTIONS:
        logger.warning("Bank exchange completed but next rate is unavailable: manor_id=%s", manor.pk, exc_info=True)
        next_rate = None

    return {
        "quantity": quantity,
        "total_cost": cost_info["total_cost"],
        "base_cost": cost_info["base_cost"],
        "fee": cost_info["fee"],
        "avg_rate": cost_info["avg_rate"],
        "rate_details": cost_info["rate_details"],
        "next_rate": next_rate,
    }


def build_bank_info_payload(
    manor,
    *,
    get_effective_gold_supply_data,
    resolve_pricing_status,
    get_today_exchange_count,
    calculate_supply_factor_from_supply,
    calculate_dynamic_rate,
    calculate_next_rate,
    calculate_progressive_factor,
    calculate_gold_bar_cost,
    gold_bar_base_price: int,
    gold_bar_fee_rate,
    gold_bar_min_price: int,
    gold_bar_max_price: int,
) -> dict[str, object]:
    effective_supply, pricing_source = get_effective_gold_supply_data()
    pricing_degraded, pricing_status_message = resolve_pricing_status(pricing_source)

    today_count = get_today_exchange_count(manor)
    supply_factor = calculate_supply_factor_from_supply(effective_supply)
    current_rate = calculate_dynamic_rate(manor, supply_factor=supply_factor)
    next_rate = calculate_next_rate(manor, supply_factor=supply_factor)
    progressive_factor = calculate_progressive_factor(today_count)
    cost_info = calculate_gold_bar_cost(manor, 1, supply_factor=supply_factor)

    return {
        "gold_bar_base_price": gold_bar_base_price,
        "gold_bar_fee_rate": float(gold_bar_fee_rate) * 100,
        "gold_bar_min_price": gold_bar_min_price,
        "gold_bar_max_price": gold_bar_max_price,
        "current_rate": current_rate,
        "next_rate": next_rate,
        "total_cost_per_bar": cost_info["total_cost"],
        "supply_factor": round(supply_factor, 3),
        "progressive_factor": round(progressive_factor, 3),
        "effective_supply": effective_supply,
        "pricing_source": pricing_source,
        "pricing_degraded": pricing_degraded,
        "pricing_status_message": pricing_status_message,
        "exchange_available": not pricing_degraded,
        "today_count": today_count,
        "manor_silver": manor.silver,
    }
