"""
钱庄利率计算

从 bank_service 中提取的汇率相关读逻辑与费用计算。
本模块不依赖 exchange 交易流程，可被 bank_service 重新导出。
"""

import logging

from core.utils import safe_int
from gameplay.models import Manor

from . import bank_supply_runtime as _bank_supply_runtime
from .bank_pricing import (
    GOLD_BAR_BASE_PRICE,
    GOLD_BAR_FEE_RATE,
    GOLD_BAR_MAX_PRICE,
    GOLD_BAR_MIN_PRICE,
    _calculate_supply_factor_from_supply,
    _normalize_positive_quantity,
    calculate_progressive_factor,
)

logger = logging.getLogger(__name__)


configure_bank_service_hooks = _bank_supply_runtime.configure_bank_service_hooks
get_effective_gold_supply_data = _bank_supply_runtime.get_effective_gold_supply_data
get_today_exchange_count_value = _bank_supply_runtime.get_today_exchange_count_value
normalize_supply_value = _bank_supply_runtime.normalize_supply_value
resolve_calculate_supply_factor = _bank_supply_runtime.resolve_calculate_supply_factor
resolve_get_effective_gold_supply = _bank_supply_runtime.resolve_get_effective_gold_supply


_get_effective_gold_supply_data = get_effective_gold_supply_data
_normalize_supply_value = normalize_supply_value


def get_effective_gold_supply(*, fail_closed: bool = False) -> int:
    return get_effective_gold_supply_data(fail_closed=fail_closed)[0]


def calculate_supply_factor(*, fail_closed: bool = False) -> float:
    """
    计算总量系数

    基于活跃玩家的金条总量，使用对数函数平滑调节：
    - 金条总量 < 基准量：系数 < 1（价格降低，鼓励兑换）
    - 金条总量 > 基准量：系数 > 1（价格上涨，抑制兑换）

    Returns:
        float: 总量系数，范围 0.85 ~ 1.40
    """
    get_supply = resolve_get_effective_gold_supply(get_effective_gold_supply)
    total_supply = get_supply(fail_closed=fail_closed) if fail_closed else get_supply()
    return _calculate_supply_factor_from_supply(total_supply)


def calculate_dynamic_rate(manor: Manor, *, supply_factor: float | None = None, fail_closed: bool = False) -> int:
    """
    计算当前动态汇率

    公式：实时汇率 = 基准价 × 总量系数 × 累进系数

    Args:
        manor: 庄园对象

    Returns:
        int: 当前汇率（银两/金条）
    """
    if supply_factor is None:
        supply_factor_fn = resolve_calculate_supply_factor(calculate_supply_factor)
        supply_factor = supply_factor_fn(fail_closed=fail_closed) if fail_closed else supply_factor_fn()
    today_count = get_today_exchange_count_value(manor)
    progressive_factor = calculate_progressive_factor(today_count)

    rate = int(GOLD_BAR_BASE_PRICE * supply_factor * progressive_factor)
    return max(GOLD_BAR_MIN_PRICE, min(GOLD_BAR_MAX_PRICE, rate))


def calculate_next_rate(manor: Manor, *, supply_factor: float | None = None, fail_closed: bool = False) -> int:
    """
    计算下一根金条的汇率（用于显示）

    Args:
        manor: 庄园对象

    Returns:
        int: 下一根金条的汇率
    """
    if supply_factor is None:
        supply_factor_fn = resolve_calculate_supply_factor(calculate_supply_factor)
        supply_factor = supply_factor_fn(fail_closed=fail_closed) if fail_closed else supply_factor_fn()
    today_count = get_today_exchange_count_value(manor)
    progressive_factor = calculate_progressive_factor(today_count + 1)

    rate = int(GOLD_BAR_BASE_PRICE * supply_factor * progressive_factor)
    return max(GOLD_BAR_MIN_PRICE, min(GOLD_BAR_MAX_PRICE, rate))


def calculate_gold_bar_cost(
    manor: Manor,
    quantity: int,
    *,
    supply_factor: float | None = None,
    fail_closed: bool = False,
) -> dict:
    """
    计算兑换金条所需银两（含手续费）

    优化：
    - 修复了 O(N) 性能问题，针对累进系数封顶（12根）后的计算进行数学优化
    - 即使购买 10000 根也能瞬间完成计算

    Args:
        manor: 庄园对象
        quantity: 兑换数量

    Returns:
        dict: 包含各项费用明细
    """
    quantity = _normalize_positive_quantity(quantity)
    if supply_factor is None:
        supply_factor_fn = resolve_calculate_supply_factor(calculate_supply_factor)
        supply_factor = supply_factor_fn(fail_closed=fail_closed) if fail_closed else supply_factor_fn()
    today_count = max(0, safe_int(get_today_exchange_count_value(manor), 0))

    base_cost = 0
    rate_details: list[int] = []

    calculated_count = 0
    current_idx = 0

    while calculated_count < quantity:
        current_count = today_count + current_idx
        if current_count >= 12:
            break

        progressive_factor = calculate_progressive_factor(current_count)
        rate = int(GOLD_BAR_BASE_PRICE * supply_factor * progressive_factor)
        rate = max(GOLD_BAR_MIN_PRICE, min(GOLD_BAR_MAX_PRICE, rate))

        base_cost += rate
        if len(rate_details) < 10:
            rate_details.append(rate)

        calculated_count += 1
        current_idx += 1

    remaining = quantity - calculated_count
    if remaining > 0:
        capped_factor = 1.60
        capped_rate = int(GOLD_BAR_BASE_PRICE * supply_factor * capped_factor)
        capped_rate = max(GOLD_BAR_MIN_PRICE, min(GOLD_BAR_MAX_PRICE, capped_rate))

        base_cost += capped_rate * remaining

        if len(rate_details) < 10:
            rate_details.append(capped_rate)

    fee = int(base_cost * GOLD_BAR_FEE_RATE)
    total_cost = base_cost + fee

    return {
        "base_cost": base_cost,
        "fee": fee,
        "total_cost": total_cost,
        "rate_details": rate_details,
        "avg_rate": base_cost // quantity if quantity > 0 else 0,
    }
