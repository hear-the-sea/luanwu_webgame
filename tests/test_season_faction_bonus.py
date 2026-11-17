from types import SimpleNamespace

import pytest

from gameplay.services.season.faction_bonus import apply_faction_bonus, battle_hp, real_hp, source_key
from gameplay.services.season.fatigue import fatigue_snapshot
from guests.guest_rules import compute_guest_attack_attribute_component, compute_guest_max_hp
from guests.models import GuestTemplate


def test_matching_guest_bonus_preserves_equipment_and_scales_health():
    guest = SimpleNamespace(
        template=SimpleNamespace(key="hero_test", base_hp=400),
        hp_bonus=0,
        force=100,
        intellect=50,
        defense_stat=80,
        agility=30,
        luck=20,
        archetype="military",
        level=1,
    )
    snapshot = {key: getattr(guest, key) for key in ("force", "intellect", "defense_stat", "agility", "luck")}
    snapshot.update(
        attack=100,
        attack_attribute_component=compute_guest_attack_attribute_component(guest),
        defense=100,
        max_hp=compute_guest_max_hp(guest),
        current_hp=2200,
    )
    apply_faction_bonus(guest, snapshot, {"hero_test"})
    assert (
        snapshot["force"],
        snapshot["intellect"],
        snapshot["defense_stat"],
        snapshot["agility"],
        snapshot["luck"],
    ) == (110, 55, 88, 33, 22)
    assert snapshot["defense"] == 108
    assert snapshot["max_hp"] == 4800
    assert snapshot["current_hp"] == 2400
    assert real_hp(snapshot, 2400) == 2200
    assert battle_hp(snapshot, 4400) == 4800
    assert guest.defense_stat == 80


def test_nonmatching_guest_is_unchanged():
    snapshot = {"force": 100}
    apply_faction_bonus(SimpleNamespace(template=SimpleNamespace(key="other")), snapshot, {"hero_test"})
    assert snapshot == {"force": 100}


def test_health_conversion_round_trip_never_heals():
    snapshot = {"faction_bonus_base_hp": 4400, "max_hp": 4800}
    for health in range(1, 4401):
        assert real_hp(snapshot, battle_hp(snapshot, health)) == health
    assert real_hp(snapshot, 0) == 0
    assert real_hp(snapshot, 1) == 1


@pytest.mark.django_db
def test_converted_and_special_templates_keep_original_faction():
    source = GuestTemplate.objects.create(key="hero_test", name="测试", rarity="green", archetype="military")
    converted = SimpleNamespace(key=f"guest_rarity_conversion_blue_{source.pk}")
    assert source_key(converted) == "hero_test"
    assert source_key(SimpleNamespace(key="hist_sljnbc_0589_purple")) == "hist_sljnbc_0589"


def test_fatigue_recomputes_civil_attack_from_boosted_civil_attributes():
    run = SimpleNamespace(
        member=SimpleNamespace(
            season=SimpleNamespace(
                rules={
                    "campaign": {
                        "fortifications": {"enabled": True, "fatigue": {"max_stacks": 2, "per_stack_percent": 5}}
                    }
                }
            )
        ),
        result={"fatigue_stacks": 1},
    )
    snapshot = {
        "archetype": "civil",
        "force": 110,
        "intellect": 220,
        "agility": 110,
        "defense_stat": 110,
        "defense": 130,
        "attack_attribute_component": 165,
        "attack": 185,
    }
    fatigued = fatigue_snapshot(run, snapshot)
    assert fatigued["attack_attribute_component"] == (104 + 209) // 2
    assert fatigued["attack"] == (104 + 209) // 2 + 20
    assert snapshot["attack"] == 185
