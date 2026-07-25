from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from core.exceptions import TroopRecruitmentAlreadyInProgressError, TroopRecruitmentError
from gameplay.models import InventoryItem, TroopRecruitment
from gameplay.services.recruitment.recruitment import _consume_equipment_for_recruitment, start_troop_recruitment
from tests.troop_recruitment_service.support import create_tool_template, set_inventory

pytest_plugins = ("tests.troop_recruitment_service.fixtures",)


@pytest.mark.django_db
def test_consume_equipment_batch_update_and_delete(recruit_manor):
    manor = recruit_manor
    spear = create_tool_template("equip_spear", "长枪")
    shield = create_tool_template("equip_shield", "盾牌")
    set_inventory(manor, spear, 5)
    set_inventory(manor, shield, 2)

    costs = _consume_equipment_for_recruitment(manor, ["equip_spear", "equip_shield"], quantity=2)

    assert costs == {"equip_spear": 2, "equip_shield": 2}
    spear_item = InventoryItem.objects.get(
        manor=manor,
        template=spear,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    assert spear_item.quantity == 3
    assert not InventoryItem.objects.filter(
        manor=manor,
        template=shield,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    ).exists()


@pytest.mark.django_db
def test_consume_equipment_insufficient_keeps_inventory_unchanged(recruit_manor):
    manor = recruit_manor
    spear = create_tool_template("equip_spear_short", "短枪")
    shield = create_tool_template("equip_shield_short", "短盾")
    set_inventory(manor, spear, 5)
    set_inventory(manor, shield, 1)

    with pytest.raises(TroopRecruitmentError, match="所需装备不足"):
        _consume_equipment_for_recruitment(manor, ["equip_spear_short", "equip_shield_short"], quantity=2)

    spear_item = InventoryItem.objects.get(
        manor=manor,
        template=spear,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    shield_item = InventoryItem.objects.get(
        manor=manor,
        template=shield,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    assert spear_item.quantity == 5
    assert shield_item.quantity == 1


@pytest.mark.django_db
def test_start_troop_recruitment_deducts_inventory_and_retainers(monkeypatch, recruit_manor):
    manor = recruit_manor
    spear = create_tool_template("equip_spear_recruit", "募兵长枪")
    shield = create_tool_template("equip_shield_recruit", "募兵盾牌")
    set_inventory(manor, spear, 4)
    set_inventory(manor, shield, 2)

    troop_data = {
        "name": "长枪兵",
        "recruit": {
            "equipment": ["equip_spear_recruit", "equip_shield_recruit"],
            "retainer_cost": 2,
            "base_duration": 60,
        },
    }

    schedule_calls = []

    monkeypatch.setattr(
        "gameplay.services.recruitment.recruitment._validate_start_recruitment_inputs",
        lambda current_manor, troop_key, quantity: troop_data,
    )
    monkeypatch.setattr(
        "gameplay.services.recruitment.recruitment._schedule_recruitment_completion",
        lambda recruitment, eta_seconds: schedule_calls.append((recruitment.id, eta_seconds)),
    )
    monkeypatch.setattr("gameplay.services.recruitment.recruitment.calculate_recruitment_duration", lambda *_args: 30)

    recruitment = start_troop_recruitment(manor, "spearman", quantity=2)

    manor.refresh_from_db()
    assert manor.retainer_count == 16
    assert recruitment.retainer_cost == 4
    assert recruitment.equipment_costs == {"equip_spear_recruit": 2, "equip_shield_recruit": 2}
    assert recruitment.actual_duration == 60
    assert recruitment.status == TroopRecruitment.Status.RECRUITING
    assert schedule_calls == [(recruitment.id, recruitment.actual_duration)]

    spear_item = InventoryItem.objects.get(
        manor=manor,
        template=spear,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    assert spear_item.quantity == 2
    assert not InventoryItem.objects.filter(
        manor=manor,
        template=shield,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    ).exists()


@pytest.mark.django_db
def test_start_troop_recruitment_rollback_on_insufficient_equipment(monkeypatch, recruit_manor):
    manor = recruit_manor
    spear = create_tool_template("equip_spear_rollback", "回滚长枪")
    shield = create_tool_template("equip_shield_rollback", "回滚盾牌")
    set_inventory(manor, spear, 3)
    set_inventory(manor, shield, 1)

    troop_data = {
        "name": "回滚兵",
        "recruit": {
            "equipment": ["equip_spear_rollback", "equip_shield_rollback"],
            "retainer_cost": 3,
            "base_duration": 90,
        },
    }

    monkeypatch.setattr(
        "gameplay.services.recruitment.recruitment._validate_start_recruitment_inputs",
        lambda current_manor, troop_key, quantity: troop_data,
    )

    with pytest.raises(TroopRecruitmentError, match="所需装备不足"):
        start_troop_recruitment(manor, "rollback_unit", quantity=2)

    manor.refresh_from_db()
    assert manor.retainer_count == 20
    assert TroopRecruitment.objects.filter(manor=manor).count() == 0

    spear_item = InventoryItem.objects.get(
        manor=manor,
        template=spear,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    shield_item = InventoryItem.objects.get(
        manor=manor,
        template=shield,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    assert spear_item.quantity == 3
    assert shield_item.quantity == 1


@pytest.mark.django_db
def test_start_troop_recruitment_rechecks_active_queue_after_lock(monkeypatch, recruit_manor):
    manor = recruit_manor
    stale_manor = type(manor).objects.get(pk=manor.pk)

    troop_data = {
        "name": "长枪兵",
        "recruit": {
            "equipment": [],
            "retainer_cost": 2,
            "base_duration": 60,
        },
    }

    monkeypatch.setattr(
        "gameplay.services.recruitment.recruitment._validate_start_recruitment_inputs",
        lambda current_manor, troop_key, quantity: troop_data,
    )
    monkeypatch.setattr(
        "gameplay.services.recruitment.recruitment._schedule_recruitment_completion",
        lambda recruitment, eta_seconds: None,
    )

    TroopRecruitment.objects.create(
        manor=manor,
        troop_key="existing_spearman",
        troop_name="现有长枪兵",
        quantity=1,
        equipment_costs={},
        retainer_cost=1,
        base_duration=60,
        actual_duration=60,
        complete_at=timezone.now() + timedelta(minutes=1),
    )

    with pytest.raises(TroopRecruitmentAlreadyInProgressError, match="已有募兵正在进行中"):
        start_troop_recruitment(stale_manor, "spearman", quantity=1)

    manor.refresh_from_db()
    assert manor.retainer_count == 20
    assert TroopRecruitment.objects.filter(manor=manor, status=TroopRecruitment.Status.RECRUITING).count() == 1


@pytest.mark.django_db
def test_start_troop_recruitment_uses_locked_retainer_count_instead_of_stale_object(monkeypatch, recruit_manor):
    manor = recruit_manor
    stale_manor = type(manor).objects.get(pk=manor.pk)
    type(manor).objects.filter(pk=manor.pk).update(retainer_count=1)

    troop_data = {
        "name": "刀盾兵",
        "recruit": {
            "equipment": [],
            "retainer_cost": 2,
            "base_duration": 60,
        },
    }

    monkeypatch.setattr(
        "gameplay.services.recruitment.recruitment._validate_start_recruitment_inputs",
        lambda current_manor, troop_key, quantity: troop_data,
    )
    monkeypatch.setattr(
        "gameplay.services.recruitment.recruitment._schedule_recruitment_completion",
        lambda recruitment, eta_seconds: None,
    )

    with pytest.raises(TroopRecruitmentError, match="家丁不足，需要2"):
        start_troop_recruitment(stale_manor, "shield_bearer", quantity=1)

    manor.refresh_from_db()
    assert manor.retainer_count == 1
    assert TroopRecruitment.objects.filter(manor=manor).count() == 0
