from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.exceptions import JailError
from gameplay.services.jail_persuasion.eligibility import recruitment_offer, recruitment_success_percent


def _prisoner(*, loyalty=30, affinity=0, rarity="green", is_hermit=False, milestone_stage=0):
    return SimpleNamespace(
        loyalty=loyalty,
        affinity=affinity,
        milestone_stage=milestone_stage,
        guest_template=SimpleNamespace(rarity=rarity, is_hermit=is_hermit),
    )


def test_standard_recruitment_keeps_legacy_cost_and_loyalty():
    offer = recruitment_offer(_prisoner(loyalty=30), "standard")
    assert (offer.eligible, offer.gold_cost, offer.initial_loyalty) == (True, 1, 35)
    assert recruitment_offer(_prisoner(loyalty=31), "standard").eligible is False


def test_recruitment_modes_use_requested_initial_loyalty_values():
    offers = {
        "standard": recruitment_offer(_prisoner(loyalty=30), "standard"),
        "negotiated": recruitment_offer(_prisoner(loyalty=45, affinity=60), "negotiated"),
        "heartfelt": recruitment_offer(_prisoner(loyalty=80, affinity=100), "heartfelt"),
    }

    assert {mode: offer.initial_loyalty for mode, offer in offers.items()} == {
        "standard": 35,
        "negotiated": 50,
        "heartfelt": 65,
    }


def test_negotiated_recruitment_uses_heart_affinity_without_rarity_surcharge():
    offer = recruitment_offer(_prisoner(loyalty=45, affinity=60, rarity="orange"), "negotiated")
    assert (offer.eligible, offer.gold_cost, offer.initial_loyalty) == (True, 1, 50)
    assert recruitment_offer(_prisoner(loyalty=46, affinity=60), "negotiated").eligible is False
    assert recruitment_offer(_prisoner(loyalty=45, affinity=59), "negotiated").eligible is False


def test_heartfelt_recruitment_scales_cost_and_applies_milestone_discount():
    offer = recruitment_offer(
        _prisoner(loyalty=80, affinity=100, rarity="purple", milestone_stage=2),
        "heartfelt",
    )
    assert (offer.eligible, offer.gold_cost, offer.initial_loyalty) == (True, 3, 65)
    assert recruitment_offer(_prisoner(affinity=99), "heartfelt").eligible is False


def test_black_hermit_does_not_increase_recruitment_cost():
    offer = recruitment_offer(
        _prisoner(loyalty=45, affinity=60, rarity="black", is_hermit=True),
        "negotiated",
    )
    assert offer.gold_cost == 1


@pytest.mark.parametrize(
    ("mode", "loyalty", "affinity", "rarity", "is_hermit", "expected"),
    [
        ("standard", 30, 0, "green", False, 25),
        ("standard", 15, 0, "green", False, 35),
        ("standard", 0, 0, "green", False, 45),
        ("negotiated", 45, 60, "green", False, 55),
        ("negotiated", 22, 80, "green", False, 65),
        ("negotiated", 0, 100, "green", False, 75),
        ("heartfelt", 100, 100, "green", False, 90),
        ("heartfelt", 50, 100, "green", False, 95),
        ("heartfelt", 0, 100, "green", False, 100),
        ("negotiated", 0, 100, "black", False, 75),
        ("negotiated", 0, 100, "gray", False, 75),
        ("negotiated", 0, 100, "red", False, 70),
        ("negotiated", 0, 100, "blue", False, 70),
        ("negotiated", 0, 100, "purple", False, 65),
        ("negotiated", 0, 100, "orange", False, 60),
        ("standard", 30, 0, "black", True, 5),
    ],
)
def test_recruitment_success_percent(mode, loyalty, affinity, rarity, is_hermit, expected):
    prisoner = _prisoner(
        loyalty=loyalty,
        affinity=affinity,
        rarity=rarity,
        is_hermit=is_hermit,
    )

    assert recruitment_success_percent(prisoner, mode) == expected


def test_negotiated_recruitment_rounds_each_bonus_before_combining():
    prisoner = _prisoner(loyalty=43, affinity=61, rarity="green")

    assert recruitment_success_percent(prisoner, "negotiated") == 55


def test_negotiated_recruitment_rounds_half_bonus_up():
    prisoner = _prisoner(loyalty=45, affinity=62, rarity="green")

    assert recruitment_success_percent(prisoner, "negotiated") == 56


def test_unknown_recruitment_mode_is_rejected():
    with pytest.raises(JailError, match="未知的归附方式"):
        recruitment_offer(_prisoner(), "unknown")
