from __future__ import annotations

import pytest
from django.utils import timezone

from core.exceptions import MessageError
from gameplay.models import EquipmentProduction, InventoryItem, ItemTemplate
from gameplay.services.buildings import forge as forge_service
from gameplay.services.manor.core import ensure_manor


@pytest.mark.django_db
def test_start_equipment_forging_consumes_materials_and_creates_record(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="forge_service_start", password="pass12345")
    manor = ensure_manor(user)

    equipment_tpl = ItemTemplate.objects.create(
        key="equip_service_helmet",
        name="服务测试头盔",
        effect_type="equip_helmet",
        rarity="green",
    )
    tong = ItemTemplate.objects.create(key="tong", name="铜", effect_type="resource", rarity="black")
    xi = ItemTemplate.objects.create(key="xi", name="锡", effect_type="resource", rarity="black")
    InventoryItem.objects.create(manor=manor, template=tong, quantity=10)
    InventoryItem.objects.create(manor=manor, template=xi, quantity=8)

    monkeypatch.setattr(
        forge_service,
        "EQUIPMENT_CONFIG",
        {
            equipment_tpl.key: {
                "category": "helmet",
                "materials": {"tong": 3, "xi": 2},
                "base_duration": 120,
                "required_forging": 1,
            }
        },
    )
    monkeypatch.setattr("gameplay.services.technology.get_player_technology_level", lambda *_args, **_kwargs: 5)
    monkeypatch.setattr(
        "gameplay.services.buildings.forge_runtime.calculate_forging_duration", lambda _base, _manor: 90
    )

    scheduled = {}
    monkeypatch.setattr(
        "gameplay.services.buildings.forge_runtime.schedule_forging_completion",
        lambda production, duration: scheduled.update({"id": production.id, "duration": duration}),
    )

    production = forge_service.start_equipment_forging(manor, equipment_tpl.key, quantity=2)

    assert production.status == EquipmentProduction.Status.FORGING
    assert production.quantity == 2
    assert production.material_costs == {"tong": 6, "xi": 4}
    assert production.actual_duration == 180
    assert scheduled == {"id": production.id, "duration": 180}
    assert InventoryItem.objects.get(manor=manor, template=tong).quantity == 4
    assert InventoryItem.objects.get(manor=manor, template=xi).quantity == 4


@pytest.mark.django_db
def test_finalize_equipment_forging_keeps_success_when_notification_ws_fails(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="forge_service_finalize", password="pass12345")
    manor = ensure_manor(user)

    equipment_tpl = ItemTemplate.objects.create(
        key="equip_service_dao",
        name="服务测试刀",
        effect_type="equip_weapon",
        rarity="blue",
    )
    production = EquipmentProduction.objects.create(
        manor=manor,
        equipment_key=equipment_tpl.key,
        equipment_name=equipment_tpl.name,
        quantity=2,
        material_costs={"tong": 4},
        base_duration=60,
        actual_duration=60,
        complete_at=timezone.now() - timezone.timedelta(seconds=1),
        status=EquipmentProduction.Status.FORGING,
    )

    monkeypatch.setattr(
        "gameplay.services.utils.notifications.notify_user",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError("ws backend down")),
    )

    assert forge_service.finalize_equipment_forging(production, send_notification=True) is True

    production.refresh_from_db()
    assert production.status == EquipmentProduction.Status.COMPLETED
    assert InventoryItem.objects.get(manor=manor, template=equipment_tpl).quantity == 2


@pytest.mark.django_db
def test_finalize_equipment_forging_keeps_success_when_message_fails(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="forge_service_msg_fail", password="pass12345")
    manor = ensure_manor(user)

    equipment_tpl = ItemTemplate.objects.create(
        key="equip_service_sword",
        name="服务测试剑",
        effect_type="equip_weapon",
        rarity="blue",
    )
    production = EquipmentProduction.objects.create(
        manor=manor,
        equipment_key=equipment_tpl.key,
        equipment_name=equipment_tpl.name,
        quantity=2,
        material_costs={"tong": 4},
        base_duration=60,
        actual_duration=60,
        complete_at=timezone.now() - timezone.timedelta(seconds=1),
        status=EquipmentProduction.Status.FORGING,
    )

    monkeypatch.setattr(
        "gameplay.services.utils.messages.create_message",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(MessageError("message backend down")),
    )

    assert forge_service.finalize_equipment_forging(production, send_notification=True) is True

    production.refresh_from_db()
    assert production.status == EquipmentProduction.Status.COMPLETED
    assert InventoryItem.objects.get(manor=manor, template=equipment_tpl).quantity == 2


@pytest.mark.django_db
def test_finalize_equipment_forging_message_runtime_marker_error_bubbles_up(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="forge_service_msg_runtime", password="pass12345")
    manor = ensure_manor(user)

    equipment_tpl = ItemTemplate.objects.create(
        key="equip_service_axe",
        name="服务测试斧",
        effect_type="equip_weapon",
        rarity="blue",
    )
    production = EquipmentProduction.objects.create(
        manor=manor,
        equipment_key=equipment_tpl.key,
        equipment_name=equipment_tpl.name,
        quantity=2,
        material_costs={"tong": 4},
        base_duration=60,
        actual_duration=60,
        complete_at=timezone.now() - timezone.timedelta(seconds=1),
        status=EquipmentProduction.Status.FORGING,
    )

    monkeypatch.setattr(
        "gameplay.services.utils.messages.create_message",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("message backend down")),
    )

    with pytest.raises(RuntimeError, match="message backend down"):
        forge_service.finalize_equipment_forging(production, send_notification=True)

    production.refresh_from_db()
    assert production.status == EquipmentProduction.Status.COMPLETED


@pytest.mark.django_db
def test_finalize_equipment_forging_notification_runtime_marker_error_bubbles_up(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="forge_service_ws_runtime", password="pass12345")
    manor = ensure_manor(user)

    equipment_tpl = ItemTemplate.objects.create(
        key="equip_service_spear",
        name="服务测试枪",
        effect_type="equip_weapon",
        rarity="blue",
    )
    production = EquipmentProduction.objects.create(
        manor=manor,
        equipment_key=equipment_tpl.key,
        equipment_name=equipment_tpl.name,
        quantity=2,
        material_costs={"tong": 4},
        base_duration=60,
        actual_duration=60,
        complete_at=timezone.now() - timezone.timedelta(seconds=1),
        status=EquipmentProduction.Status.FORGING,
    )

    monkeypatch.setattr("gameplay.services.utils.messages.create_message", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        "gameplay.services.buildings.forge_runtime.notify_user",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("ws backend down")),
    )

    with pytest.raises(RuntimeError, match="ws backend down"):
        forge_service.finalize_equipment_forging(production, send_notification=True)

    production.refresh_from_db()
    assert production.status == EquipmentProduction.Status.COMPLETED


@pytest.mark.django_db
def test_start_equipment_forging_rejects_malformed_runtime_config(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="forge_service_bad_config", password="pass12345")
    manor = ensure_manor(user)

    monkeypatch.setattr(
        forge_service,
        "EQUIPMENT_CONFIG",
        {
            "equip_service_bad": {
                "category": "helmet",
                "materials": "bad",
                "base_duration": 120,
                "required_forging": 1,
            }
        },
    )
    monkeypatch.setattr("gameplay.services.technology.get_player_technology_level", lambda *_args, **_kwargs: 5)

    with pytest.raises(AssertionError, match="invalid forge runtime equipment config equip_service_bad materials"):
        forge_service.start_equipment_forging(manor, "equip_service_bad", quantity=1)
