from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.exceptions import JailError
from gameplay.services.jail_persuasion.eligibility import recruitment_offer


def _prisoner(*, loyalty=30, affinity=0, rarity="green", is_hermit=False, milestone_stage=0):
    return SimpleNamespace(
        loyalty=loyalty,
        affinity=affinity,
        milestone_stage=milestone_stage,
        guest_template=SimpleNamespace(rarity=rarity, is_hermit=is_hermit),
    )


def test_standard_recruitment_keeps_legacy_cost_and_loyalty():
    offer = recruitment_offer(_prisoner(loyalty=30), "standard")
    assert (offer.eligible, offer.gold_cost, offer.initial_loyalty) == (True, 1, 60)
    assert recruitment_offer(_prisoner(loyalty=31), "standard").eligible is False


def test_negotiated_recruitment_uses_heart_affinity_and_rarity_surcharge():
    offer = recruitment_offer(_prisoner(loyalty=45, affinity=60, rarity="orange"), "negotiated")
    assert (offer.eligible, offer.gold_cost, offer.initial_loyalty) == (True, 4, 65)
    assert recruitment_offer(_prisoner(loyalty=46, affinity=60), "negotiated").eligible is False
    assert recruitment_offer(_prisoner(loyalty=45, affinity=59), "negotiated").eligible is False


def test_heartfelt_recruitment_scales_cost_and_applies_milestone_discount():
    offer = recruitment_offer(
        _prisoner(loyalty=80, affinity=100, rarity="purple", milestone_stage=2),
        "heartfelt",
    )
    assert (offer.eligible, offer.gold_cost, offer.initial_loyalty) == (True, 5, 75)
    assert recruitment_offer(_prisoner(affinity=99), "heartfelt").eligible is False


def test_black_hermit_uses_override_surcharge():
    offer = recruitment_offer(
        _prisoner(loyalty=45, affinity=60, rarity="black", is_hermit=True),
        "negotiated",
    )
    assert offer.gold_cost == 5


def test_unknown_recruitment_mode_is_rejected():
    with pytest.raises(JailError, match="未知的归附方式"):
        recruitment_offer(_prisoner(), "unknown")
