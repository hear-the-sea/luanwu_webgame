"""
训练计算工具模块

提供门客训练相关的成本和时间计算函数。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, cast

if TYPE_CHECKING:
    from ..models import Guest

from core.config import GUEST
from core.utils.time_scale import scale_duration

from ..models import GuestRarity

# 稀有度训练时间系数
RARITY_TIME_COEFFICIENT = {
    GuestRarity.BLACK: 1.0,
    GuestRarity.GRAY: 1.1,
    GuestRarity.GREEN: 1.2,
    GuestRarity.RED: 1.25,
    GuestRarity.BLUE: 1.3,
    GuestRarity.PURPLE: 1.5,
    GuestRarity.ORANGE: 1.6,
}

# 基础训练时间（秒）
BASE_TRAINING_TIME = 1800  # 黑色品质1→2级需要1800秒
TRAINING_LEVEL_GROWTH_RATE = 0.07  # 每提升一级，训练时间指数增长7%

# 成本配置
GRAIN_COST_PER_LEVEL = 120  # 每级粮食基础消耗
SILVER_COST_PER_LEVEL = 50  # 每级银两消耗


def calculate_level_up_cost(current_level: int, target_levels: int = 1) -> Dict[str, int]:
    """
    计算门客升级所需的资源成本。

    成本计算公式：
    - 粮食：sum((当前等级 + i) * 120) for i in [1, target_levels]
    - 银两：target_levels * 50

    Args:
        current_level: 当前等级
        target_levels: 要升级的等级数

    Returns:
        资源成本字典 {"grain": 粮食数量, "silver": 银两数量}

    Raises:
        AssertionError: 如果参数不合法或升级后超过最大等级

    Examples:
        >>> calculate_level_up_cost(1, 1)
        {'grain': 240, 'silver': 50}
        >>> calculate_level_up_cost(5, 2)
        {'grain': 1320, 'silver': 100}
    """
    # 安全修复：验证参数范围，防止负数导致异常行为
    if current_level < 1:
        raise AssertionError(f"当前等级必须>=1，收到: {current_level}")
    if target_levels < 1:
        raise AssertionError(f"升级等级数必须>=1，收到: {target_levels}")

    target_level = current_level + target_levels
    max_guest_level = int(GUEST.MAX_LEVEL)
    if target_level > max_guest_level:
        raise AssertionError(f"已达等级上限 {max_guest_level}")

    # 粮食成本随等级递增
    grain_cost = sum((current_level + i) * GRAIN_COST_PER_LEVEL for i in range(1, target_levels + 1))
    # 银两成本固定
    silver_cost = target_levels * SILVER_COST_PER_LEVEL

    return {"grain": grain_cost, "silver": silver_cost}


def calculate_training_duration(current_level: int, rarity: str, levels: int = 1) -> int:
    """
    计算门客训练所需的时间（秒）。

    训练时间受稀有度和等级影响：
    - 稀有度越高，训练时间越长（通过系数调整）
    - 等级越高，训练时间越长（每级指数增长7%）

    基准：黑色品质1→2级需要1800秒

    Args:
        current_level: 当前等级
        rarity: 稀有度
        levels: 要训练的等级数

    Returns:
        训练所需总秒数

    Raises:
        AssertionError: 如果参数不合法

    Examples:
        >>> calculate_training_duration(1, 'black', 1)
        1800
        >>> calculate_training_duration(1, 'purple', 1)
        2700
        >>> calculate_training_duration(10, 'black', 1)
        3309
    """
    if current_level < 1:
        raise AssertionError(f"当前等级必须>=1，收到: {current_level}")
    if levels < 1:
        raise AssertionError(f"训练等级数必须>=1，收到: {levels}")

    # `rarity` is usually a string (GuestRarity values), while the coefficient
    # map is keyed by those enum values.
    rarity_coeff = RARITY_TIME_COEFFICIENT.get(cast(GuestRarity, rarity), 1.0)
    total = 0

    for i in range(levels):
        level = current_level + i
        # 基础时间 * 稀有度系数
        base = BASE_TRAINING_TIME * rarity_coeff
        # 等级成长系数：从1级到当前等级按7%指数增长
        growth = (1 + TRAINING_LEVEL_GROWTH_RATE) ** max(0, level - 1)
        total += int(base * growth)

    return scale_duration(total, minimum=1)


def get_level_up_cost(guest: "Guest", levels: int = 1) -> Dict[str, int]:
    """
    获取指定门客升级所需成本（便捷包装函数）。

    Args:
        guest: 门客实例
        levels: 要升级的等级数

    Returns:
        资源成本字典
    """
    return calculate_level_up_cost(guest.level, levels)


def get_training_duration(guest: "Guest", levels: int = 1) -> int:
    """
    获取指定门客训练所需时间（便捷包装函数）。

    Args:
        guest: 门客实例
        levels: 要训练的等级数

    Returns:
        训练所需秒数
    """
    return calculate_training_duration(guest.level, guest.rarity, levels)
