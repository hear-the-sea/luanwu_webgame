from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from battle.models import TroopTemplate
from core.exceptions import TroopCapacityFullError
from gameplay.models import Manor, PlayerTroop, TroopBankStorage, TroopRecruitment
from gameplay.services.manor.core import ensure_manor
from gameplay.services.virtual_player_core.prestige_targets import (
    virtual_core_building_level_for_prestige_band,
    virtual_juxianzhuang_level_for_prestige_band,
)
from gameplay.services.virtual_player_core.troop_capacity import (
    ensure_virtual_troop_capacity_locked,
    get_virtual_troop_remaining_space,
    get_virtual_troop_used_space,
    virtual_troop_capacity_for_prestige_band,
)


def _policy_payload() -> dict[str, object]:
    return {
        "starter_snapshots": {
            "profiles": {
                "newbie": {"troop_total": 500, "juxianzhuang_level": 2},
                "junior": {"troop_total": 1_000, "juxianzhuang_level": 3},
                "middle": {"troop_total": 1_500, "juxianzhuang_level": 4},
                "senior": {"troop_total": 2_000, "juxianzhuang_level": 5},
                "veteran": {"troop_total": 2_500, "juxianzhuang_level": 6},
                "elite": {"troop_total": 3_000, "juxianzhuang_level": 7},
                "legend": {"troop_total": 3_500, "juxianzhuang_level": 8},
                "mythic": {"troop_total": 4_000, "juxianzhuang_level": 9},
            }
        }
    }


@pytest.mark.parametrize(
    ("prestige_band", "expected"),
    (
        ("newbie", 500),
        ("junior", 1_000),
        ("middle", 1_500),
        ("senior", 2_000),
        ("veteran", 2_500),
        ("elite", 3_000),
        ("legend", 3_500),
        ("mythic", 4_000),
    ),
)
def test_virtual_troop_capacity_uses_the_eight_prestige_band_gradient(prestige_band, expected):
    assert (
        virtual_troop_capacity_for_prestige_band(
            policy_payload=_policy_payload(),
            prestige_band=prestige_band,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("prestige_band", "expected"),
    (
        ("newbie", 2),
        ("junior", 3),
        ("middle", 4),
        ("senior", 5),
        ("veteran", 6),
        ("elite", 7),
        ("legend", 8),
        ("mythic", 9),
    ),
)
def test_virtual_core_building_target_uses_the_published_prestige_gradient(prestige_band, expected):
    payload = _policy_payload()
    payload["starter_snapshots"]["profiles"][prestige_band]["core_building_level"] = expected + 100

    assert (
        virtual_juxianzhuang_level_for_prestige_band(
            policy_payload=payload,
            prestige_band=prestige_band,
        )
        == expected
    )
    assert (
        virtual_core_building_level_for_prestige_band(
            policy_payload=payload,
            prestige_band=prestige_band,
        )
        == expected
    )


def test_virtual_juxianzhuang_target_supports_legacy_core_building_fallback() -> None:
    payload = {"starter_snapshots": {"profiles": {"newbie": {"core_building_level": 2}}}}

    assert (
        virtual_juxianzhuang_level_for_prestige_band(
            policy_payload=payload,
            prestige_band="newbie",
        )
        == 2
    )


def _troop_template(manor: Manor) -> TroopTemplate:
    return TroopTemplate.objects.create(
        key=f"virtual_capacity_{manor.id}",
        name="声望容量测试护院",
        description="",
        base_attack=10,
        base_defense=10,
        base_hp=10,
        speed_bonus=0,
        priority=0,
        default_count=0,
    )


@pytest.mark.django_db(transaction=True)
def test_virtual_troop_remaining_space_counts_manor_bank_and_pending_recruitment(django_user_model):
    user = django_user_model.objects.create_user(username="virtual_troop_capacity_total")
    manor = ensure_manor(user)
    template = _troop_template(manor)
    PlayerTroop.objects.create(manor=manor, troop_template=template, count=400)
    TroopBankStorage.objects.create(manor=manor, troop_template=template, count=200)
    TroopRecruitment.objects.create(
        manor=manor,
        troop_key=template.key,
        troop_name=template.name,
        quantity=100,
        retainer_cost=0,
        base_duration=60,
        actual_duration=60,
        complete_at=timezone.now() + timedelta(minutes=1),
    )

    assert get_virtual_troop_used_space(manor) == 700
    assert get_virtual_troop_remaining_space(manor, virtual_capacity=1_000) == 300
    assert get_virtual_troop_remaining_space(manor, virtual_capacity=500) == 0

    with pytest.raises(TroopCapacityFullError):
        ensure_virtual_troop_capacity_locked(manor, 301, virtual_capacity=1_000)

    assert ensure_virtual_troop_capacity_locked(manor, 300, virtual_capacity=1_000) == 0
