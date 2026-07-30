from types import SimpleNamespace

from core.config import GUEST
from guests.guest_rules import build_guest_stat_block


def test_build_guest_stat_block_applies_attack_and_defense_bonuses():
    guest = SimpleNamespace(
        template=SimpleNamespace(base_hp=1000),
        force=400,
        intellect=200,
        defense_stat=300,
        attack_bonus=37,
        defense_bonus=19,
        hp_bonus=0,
        archetype="military",
    )

    stats = build_guest_stat_block(guest)

    expected_base_attack = int(
        guest.force * GUEST.MILITARY_FORCE_WEIGHT + guest.intellect * GUEST.MILITARY_INTELLECT_WEIGHT
    )
    assert stats["attack"] == expected_base_attack + 37
    assert stats["defense"] == 319
