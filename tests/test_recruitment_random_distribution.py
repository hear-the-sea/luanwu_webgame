"""Verify the production recruitment draw's complete integer range and replay behavior."""

import random
from collections import Counter

import pytest

from guests.utils.recruitment_utils import choose_rarity, get_recruitment_rarity_distribution


class FixedRoll:
    def __init__(self, roll):
        self.roll = roll

    def randint(self, lower, upper):
        assert (lower, upper) == (1, 1_000_000)
        assert lower <= self.roll <= upper
        return self.roll


@pytest.mark.parametrize(
    "roll, expected",
    [
        (1, "orange"),
        (150, "orange"),
        (151, "hermit"),
        (450, "hermit"),
        (451, "purple"),
        (1050, "purple"),
        (1051, "blue"),
        (3550, "blue"),
        (3551, "green"),
        (10550, "green"),
        (10551, "gray"),
        (60550, "gray"),
        (60551, "black"),
        (1_000_000, "black"),
    ],
)
def test_actual_draw_boundaries(roll, expected):
    assert choose_rarity(FixedRoll(roll)) == expected


def test_every_possible_roll_has_exact_configured_coverage():
    # 穷举全部一百万个整数，排除端点重叠、漏区间与零权重误命中。
    counts = Counter(choose_rarity(FixedRoll(roll)) for roll in range(1, 1_000_001))
    assert counts == {
        "orange": 150,
        "hermit": 300,
        "purple": 600,
        "blue": 2500,
        "green": 7000,
        "gray": 50000,
        "black": 939450,
    }
    total, _, distribution = get_recruitment_rarity_distribution()
    assert total == sum(weight for _, weight in distribution) == 1_000_000


def test_seed_replays_draws_without_reseeding_each_candidate():
    def draw_batch(seed):
        rng = random.Random(seed)
        return [choose_rarity(rng) for _ in range(10_000)]

    first = draw_batch(20261017)
    assert first == draw_batch(20261017)
    assert first != draw_batch(20261018)
    assert len(set(first)) > 1
