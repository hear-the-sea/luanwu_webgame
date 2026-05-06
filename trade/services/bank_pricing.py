"""
钱庄汇率计算 — 常量、异常与纯数学规则

本模块只包含不依赖任何 IO（缓存/数据库）的部分：
  - 金条配置常量
  - 异常类
  - 基础工具函数（_normalize_positive_quantity）
  - 纯数学汇率因子计算（_calculate_supply_factor_from_supply, calculate_progressive_factor）
"""

import math
from decimal import Decimal

from core.exceptions import GameError, TradeValidationError
from core.utils import safe_int

# ============ 金条基础配置 ============

GOLD_BAR_ITEM_KEY = "gold_bar"
GOLD_BAR_BASE_PRICE = 1_000_000  # 基准价100万银两
GOLD_BAR_FEE_RATE = Decimal("0.10")  # 10%手续费

# ============ 动态汇率配置 ============

GOLD_BAR_TARGET_SUPPLY = 1000  # 基准活跃金条量
GOLD_BAR_MIN_PRICE = 800_000  # 最低价80万
GOLD_BAR_MAX_PRICE = 1_600_000  # 最高价160万
GOLD_BAR_SUPPLY_FACTOR = 0.12  # 总量系数调节因子
GOLD_BAR_PROGRESSIVE_FACTOR = 0.05  # 累进系数：每根+5%
ACTIVE_DAYS_THRESHOLD = 14  # 活跃判定天数

# ============ 缓存配置 ============

SUPPLY_CACHE_KEY = "gold_bar:effective_supply"
SUPPLY_CACHE_TTL = 300  # 缓存5分钟
SUPPLY_STALE_CACHE_KEY = "gold_bar:effective_supply:stale"
SUPPLY_STALE_CACHE_TTL = 3600  # 过期缓存保留1小时，用于降级
DEGRADED_PRICING_SOURCES = frozenset({"stale_cache", "default"})


# ============ 异常 ============


class GoldBarPricingUnavailableError(GameError):
    error_code = "BANK_PRICING_UNAVAILABLE"
    default_message = "钱庄汇率暂时不可用，请稍后再试"


# ============ 基础工具函数 ============


def _normalize_positive_quantity(quantity) -> int:
    normalized = safe_int(quantity, default=0)
    if normalized <= 0:
        raise TradeValidationError("兑换数量必须大于0")
    return normalized


# ============ 纯数学因子计算 ============


def _calculate_supply_factor_from_supply(total_supply: int) -> float:
    if total_supply <= 0:
        return 0.85  # 无金条时给最低价

    ratio = total_supply / GOLD_BAR_TARGET_SUPPLY
    factor = 1 + GOLD_BAR_SUPPLY_FACTOR * math.log2(ratio)

    return max(0.85, min(1.40, factor))


def calculate_progressive_factor(today_count: int) -> float:
    """
    计算累进系数

    基于当日个人已兑换数量，每兑换一根价格上涨5%：
    - 第1根：1.05
    - 第5根：1.25
    - 第10根：1.50
    - 第12根及以上：1.60（封顶）

    Args:
        today_count: 当日已兑换数量

    Returns:
        float: 累进系数，范围 1.0 ~ 1.60
    """
    normalized_count = max(0, safe_int(today_count, default=0))
    factor = 1 + GOLD_BAR_PROGRESSIVE_FACTOR * normalized_count
    return min(factor, 1.60)
