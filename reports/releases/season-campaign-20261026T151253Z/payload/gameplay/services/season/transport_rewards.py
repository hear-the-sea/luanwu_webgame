"""有效补给按价值计奖，小额订单按比例发放基础贡献，满载上限不变。"""

from fractions import Fraction


def delivery_merit(value: int, capacity: int, maximum: int, base: int, threshold: int) -> int:
    if value <= 0:
        return 0
    base = min(base, maximum)
    base_fraction = min(Fraction(1), Fraction(value, max(1, threshold)))
    cargo_fraction = min(Fraction(1), Fraction(value, max(1, capacity)))
    return int(base * base_fraction) + int((maximum - base) * cargo_fraction)
