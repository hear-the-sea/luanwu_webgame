from __future__ import annotations

import pytest
from django.db import transaction

from battle.models import TroopTemplate
from core.exceptions import TroopCapacityFullError
from gameplay.models import Manor, PlayerTroop
from gameplay.services.manor.core import ensure_manor
from gameplay.services.raid.combat.troop_ops import _add_troops_batch as add_raid_troops_batch
from gameplay.services.recruitment.troops import _add_troops_batch as add_mission_troops_batch


def _create_troop_template(key: str) -> TroopTemplate:
    template, _ = TroopTemplate.objects.get_or_create(
        key=key,
        defaults={
            "name": "容量回归护院",
            "description": "",
            "base_attack": 10,
            "base_defense": 10,
            "base_hp": 10,
            "speed_bonus": 0,
            "priority": 0,
            "default_count": 0,
        },
    )
    return template


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "add_troops_batch",
    [add_mission_troops_batch, add_raid_troops_batch],
    ids=["mission-return", "raid-return"],
)
def test_battle_and_mission_returns_cannot_overflow_manor_capacity(django_user_model, add_troops_batch):
    user = django_user_model.objects.create_user(username=f"troop_return_{add_troops_batch.__module__}")
    manor = ensure_manor(user)
    template = _create_troop_template(f"capacity_return_{add_troops_batch.__module__.split('.')[-1]}")
    PlayerTroop.objects.create(manor=manor, troop_template=template, count=4999)

    with pytest.raises(TroopCapacityFullError, match="庄园内护院容量不足"):
        with transaction.atomic():
            locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
            add_troops_batch(locked_manor, {template.key: 2})

    assert PlayerTroop.objects.get(manor=manor, troop_template=template).count == 4999
