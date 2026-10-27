"""有效补给按价值计奖，小额订单按比例发放基础贡献，满载上限不变。"""

from fractions import Fraction


def delivery_merit(
    value: int,
    capacity: int,
    maximum: int,
    base: int,
    threshold: int,
    *,
    participation_full_value: int = 0,
    participation_merit: int = 0,
) -> int:
    if value <= 0:
        return 0
    base = min(base, maximum)
    base_fraction = min(Fraction(1), Fraction(value, max(1, threshold)))
    cargo_fraction = min(Fraction(1), Fraction(value, max(1, capacity)))
    reward = int(base * base_fraction) + int((maximum - base) * cargo_fraction)
    if participation_full_value > 0 and participation_merit > 0:
        # 小订单按真实交付价值线性增长；零/极小交付不能刷取完整保底。
        floor = int(min(Fraction(1), Fraction(value, participation_full_value)) * min(maximum, participation_merit))
        reward = max(reward, floor)
    return reward
