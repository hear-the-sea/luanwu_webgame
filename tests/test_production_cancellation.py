from __future__ import annotations

import pytest
from django.utils import timezone

from core.exceptions import ProductionCancelError
from gameplay.models import (
    EquipmentProduction,
    HorseProduction,
    InventoryItem,
    ItemTemplate,
    LivestockProduction,
    SmeltingProduction,
)
from gameplay.services.buildings import forge as forge_service
from gameplay.services.buildings import ranch as ranch_service
from gameplay.services.buildings import smithy as smithy_service
from gameplay.services.buildings import stable as stable_service

PRODUCTION_CASES = (
    pytest.param(
        HorseProduction,
        HorseProduction.Status.PRODUCING,
        stable_service.cancel_horse_production,
        stable_service.finalize_horse_production,
        {
            "horse_key": "cancel_horse_output",
            "horse_name": "取消测试马",
            "grain_cost": 80,
        },
        "horse_key",
        id="horse",
    ),
    pytest.param(
        LivestockProduction,
        LivestockProduction.Status.PRODUCING,
        ranch_service.cancel_livestock_production,
        ranch_service.finalize_livestock_production,
        {
            "livestock_key": "cancel_livestock_output",
            "livestock_name": "取消测试家畜",
            "grain_cost": 60,
        },
        "livestock_key",
        id="livestock",
    ),
    pytest.param(
        SmeltingProduction,
        SmeltingProduction.Status.PRODUCING,
        smithy_service.cancel_smelting_production,
        smithy_service.finalize_smelting_production,
        {
            "metal_key": "cancel_smelting_output",
            "metal_name": "取消测试物品",
            "cost_type": "cancel_material",
            "cost_amount": 12,
        },
        "metal_key",
        id="smelting",
    ),
    pytest.param(
        EquipmentProduction,
        EquipmentProduction.Status.FORGING,
        forge_service.cancel_equipment_forging,
        forge_service.finalize_equipment_forging,
        {
            "equipment_key": "cancel_equipment_output",
            "equipment_name": "取消测试装备",
            "material_costs": {"cancel_material": 12},
        },
        "equipment_key",
        id="equipment",
    ),
)


def _create_production(*, model, manor, active_status, fields, complete_at=None):
    return model.objects.create(
        manor=manor,
        quantity=2,
        base_duration=120,
        actual_duration=240,
        complete_at=complete_at or timezone.now() + timezone.timedelta(minutes=4),
        status=active_status,
        **fields,
    )


@pytest.mark.parametrize(
    ("model", "active_status", "cancel", "_finalize", "fields", "_output_key_field"),
    PRODUCTION_CASES,
)
@pytest.mark.django_db
def test_cancel_production_keeps_consumed_resources(
    model,
    active_status,
    cancel,
    _finalize,
    fields,
    _output_key_field,
    manor_factory,
):
    manor, _user = manor_factory()
    manor.grain = 925
    manor.silver = 880
    manor.save(update_fields=["grain", "silver"])
    material = ItemTemplate.objects.create(key="cancel_material", name="取消测试材料", effect_type="resource")
    inventory = InventoryItem.objects.create(manor=manor, template=material, quantity=17)
    production = _create_production(model=model, manor=manor, active_status=active_status, fields=fields)

    cancelled = cancel(manor, production.pk)

    production.refresh_from_db()
    manor.refresh_from_db()
    inventory.refresh_from_db()
    assert cancelled.pk == production.pk
    assert production.status == model.Status.CANCELLED
    assert production.finished_at is not None
    assert manor.grain == 925
    assert manor.silver == 880
    assert inventory.quantity == 17


@pytest.mark.parametrize(
    ("model", "active_status", "cancel", "finalize", "fields", "output_key_field"),
    PRODUCTION_CASES,
)
@pytest.mark.django_db
def test_cancelled_production_cannot_be_finalized_or_grant_output(
    model,
    active_status,
    cancel,
    finalize,
    fields,
    output_key_field,
    manor_factory,
):
    manor, _user = manor_factory()
    output_template = ItemTemplate.objects.create(
        key=fields[output_key_field],
        name="不应发放的产物",
        effect_type="resource",
    )
    production = _create_production(model=model, manor=manor, active_status=active_status, fields=fields)
    cancel(manor, production.pk)
    model.objects.filter(pk=production.pk).update(complete_at=timezone.now() - timezone.timedelta(seconds=1))
    production.refresh_from_db()

    assert finalize(production, send_notification=False) is False
    assert not InventoryItem.objects.filter(manor=manor, template=output_template).exists()


@pytest.mark.django_db
def test_cancel_production_rejects_another_manor_record(manor_factory):
    owner_manor, _owner = manor_factory()
    other_manor, _other = manor_factory()
    production = _create_production(
        model=HorseProduction,
        manor=owner_manor,
        active_status=HorseProduction.Status.PRODUCING,
        fields={
            "horse_key": "owned_horse",
            "horse_name": "归属测试马",
            "grain_cost": 10,
        },
    )

    with pytest.raises(ProductionCancelError, match="未找到该生产任务"):
        stable_service.cancel_horse_production(other_manor, production.pk)

    production.refresh_from_db()
    assert production.status == HorseProduction.Status.PRODUCING


@pytest.mark.django_db
def test_cancel_production_rejects_expired_record(manor_factory):
    manor, _user = manor_factory()
    production = _create_production(
        model=SmeltingProduction,
        manor=manor,
        active_status=SmeltingProduction.Status.PRODUCING,
        fields={
            "metal_key": "expired_output",
            "metal_name": "已到期物品",
            "cost_type": "silver",
            "cost_amount": 10,
        },
        complete_at=timezone.now() - timezone.timedelta(seconds=1),
    )

    with pytest.raises(ProductionCancelError, match="已到期"):
        smithy_service.cancel_smelting_production(manor, production.pk)

    production.refresh_from_db()
    assert production.status == SmeltingProduction.Status.PRODUCING


@pytest.mark.django_db
def test_cancel_production_is_not_repeatable(manor_factory):
    manor, _user = manor_factory()
    production = _create_production(
        model=EquipmentProduction,
        manor=manor,
        active_status=EquipmentProduction.Status.FORGING,
        fields={
            "equipment_key": "repeat_cancel_equipment",
            "equipment_name": "重复取消装备",
            "material_costs": {"cancel_material": 4},
        },
    )
    forge_service.cancel_equipment_forging(manor, production.pk)

    with pytest.raises(ProductionCancelError, match="已结束"):
        forge_service.cancel_equipment_forging(manor, production.pk)
