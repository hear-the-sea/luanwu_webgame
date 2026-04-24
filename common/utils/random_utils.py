"""
随机数工具模块

提供跨应用共享的随机数相关工具函数，包括加权选择、累计权重抽取、二项分布采样等。
"""

from __future__ import annotations

import math
import random
from typing import List, Protocol, Sequence, Tuple, TypeVar

T = TypeVar("T")


class SupportsUniform(Protocol):
    def uniform(self, a: float, b: float) -> float: ...


# 二项分布采样阈值：小于此值用精确计算，大于等于此值用正态近似
BINOMIAL_EXACT_THRESHOLD = 1000


def weighted_random_choice(
    items: Sequence[T],
    weights: Sequence[float],
    rng: SupportsUniform,
) -> T:
    """
    根据权重从列表中随机选择一个元素。

    Args:
        items: 候选元素列表
        weights: 对应的权重列表（长度须与 items 一致）
        rng: 随机数生成器

    Returns:
        被选中的元素

    Raises:
        IndexError: 如果列表为空
        AssertionError: 如果权重输入违反内部调用契约

    Examples:
        >>> import random
        >>> rng = random.Random(42)
        >>> items = ["A", "B", "C"]
        >>> weights = [1, 2, 7]  # C 有 70% 概率被选中
        >>> weighted_random_choice(items, weights, rng)
        'C'
    """
    if len(items) != len(weights):
        raise AssertionError("items 和 weights 长度必须一致")
    if not items:
        raise IndexError("候选列表不能为空")

    total = sum(weights)
    # 代码质量修复：权重全为0时抛出明确异常，而非隐式退化为均匀分布
    if total <= 0:
        raise AssertionError("权重总和必须大于 0")
    pick = rng.uniform(0, total)
    cumulative = 0.0
    for item, weight in zip(items, weights):
        cumulative += weight
        if cumulative >= pick:
            return item
    return items[-1]


def cumulative_choice(
    distribution: List[Tuple[T, int]],
    total_weight: int,
    rng: random.Random,
    default: T | None = None,
) -> T:
    """
    累计权重抽取（适用于稀有度、掉落等场景）。

    Args:
        distribution: (元素, 权重) 元组列表
        total_weight: 总权重（用于 randint 范围）
        rng: 随机数生成器
        default: 未命中时的默认返回值

    Returns:
        被选中的元素

    Examples:
        >>> import random
        >>> rng = random.Random(42)
        >>> dist = [("orange", 200), ("purple", 500), ("blue", 1000)]
        >>> cumulative_choice(dist, 10000, rng, default="black")
        'black'
    """
    roll = rng.randint(1, total_weight)
    cumulative = 0
    for item, weight in distribution:
        cumulative += weight
        if roll <= cumulative:
            return item
    return default if default is not None else distribution[-1][0]


def binomial_sample(n: int, p: float, rng: random.Random) -> int:
    """
    二项分布采样：n次独立试验，每次成功概率p，返回成功次数。

    小数量用精确计算，大数量用正态分布近似以提高性能。

    Args:
        n: 试验次数
        p: 每次成功概率
        rng: 随机数生成器

    Returns:
        成功次数

    Examples:
        >>> import random
        >>> rng = random.Random(42)
        >>> binomial_sample(100, 0.5, rng)  # 约50次成功
        47
    """
    if n <= 0:
        return 0
    if p <= 0:
        return 0
    if p >= 1:
        return n

    if n < BINOMIAL_EXACT_THRESHOLD:
        # 精确计算：逐个判断
        return sum(1 for _ in range(n) if rng.random() < p)
    else:
        # 正态近似：μ = n*p, σ = sqrt(n*p*(1-p))
        mean = n * p
        std = math.sqrt(n * p * (1 - p))
        result = rng.gauss(mean, std)
        # 限制在 [0, n] 范围内
        return max(0, min(n, round(result)))
