from __future__ import annotations

import pytest

from core.exceptions import JailError
from gameplay.models import InventoryItem, JailPrisoner
from gameplay.services.jail import recruit_prisoner
from gameplay.services.jail_persuasion.interactions import observe_prisoner

pytestmark = pytest.mark.django_db


def _set_prisoner(world, *, loyalty, affinity, stage):
    world.captor.guests.all().delete()
    prisoner = world.prisoner
    prisoner.loyalty = loyalty
    prisoner.affinity = affinity
    prisoner.milestone_stage = stage
    prisoner.save(update_fields=["loyalty", "affinity", "milestone_stage"])
    return prisoner


def _gold_quantity(world):
    return InventoryItem.objects.get(manor=world.captor, template=world.gold_template).quantity


def test_standard_recruitment_remains_default_mode(persuasion_world):
    prisoner = _set_prisoner(persuasion_world, loyalty=30, affinity=0, stage=0)
    guest = recruit_prisoner(persuasion_world.captor, prisoner.id)

    prisoner.refresh_from_db()
    assert guest.level == 1
    assert guest.loyalty == 60
    assert prisoner.status == JailPrisoner.Status.RECRUITED
    assert _gold_quantity(persuasion_world) == 9
    assert guest._recruitment_mode == "standard"
    assert guest._recruitment_copy_text


def test_negotiated_recruitment_uses_mode_cost_and_loyalty(persuasion_world):
    prisoner = _set_prisoner(persuasion_world, loyalty=45, affinity=60, stage=1)
    guest = recruit_prisoner(persuasion_world.captor, prisoner.id, mode="negotiated")

    assert guest.loyalty == 65
    assert _gold_quantity(persuasion_world) == 9
    assert guest._recruitment_mode == "negotiated"


def test_heartfelt_recruitment_uses_scaled_discounted_cost(persuasion_world):
    prisoner = _set_prisoner(persuasion_world, loyalty=80, affinity=100, stage=2)
    guest = recruit_prisoner(persuasion_world.captor, prisoner.id, mode="heartfelt")

    assert guest.loyalty == 75
    assert _gold_quantity(persuasion_world) == 7
    assert guest._recruitment_gold_cost == 3


def test_recruitment_rejects_unmet_mode_condition(persuasion_world):
    prisoner = _set_prisoner(persuasion_world, loyalty=50, affinity=59, stage=1)
    with pytest.raises(JailError, match="尚未满足权宜归附条件"):
        recruit_prisoner(persuasion_world.captor, prisoner.id, mode="negotiated")


def test_recruitment_is_blocked_by_pending_milestone(persuasion_world):
    observe_prisoner(persuasion_world.captor, persuasion_world.prisoner.id)
    prisoner = _set_prisoner(persuasion_world, loyalty=45, affinity=60, stage=0)

    with pytest.raises(JailError, match="先处理当前归心事件"):
        recruit_prisoner(persuasion_world.captor, prisoner.id, mode="negotiated")
