from __future__ import annotations

from uuid import uuid4

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from gameplay.models import InventoryItem, ItemTemplate
from guests.models import Guest, GuestArchetype, GuestRarity, GuestStatus, GuestTemplate
from guests.services.health import heal_all_guests_with_medicine


def _guest_template(*, base_hp: int = 1_000) -> GuestTemplate:
    suffix = uuid4().hex
    return GuestTemplate.objects.create(
        key=f"bulk_healing_guest_{suffix}",
        name=f"批量疗伤门客{suffix[:6]}",
        archetype=GuestArchetype.CIVIL,
        rarity=GuestRarity.GRAY,
        base_hp=base_hp,
    )


def _medicine(manor, *, heal_amount: int, quantity: int) -> InventoryItem:
    suffix = uuid4().hex
    template = ItemTemplate.objects.create(
        key=f"bulk_healing_medicine_{suffix}",
        name="批量疗伤药",
        effect_type=ItemTemplate.EffectType.MEDICINE,
        effect_payload={"hp": heal_amount},
    )
    return InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=quantity,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )


@pytest.mark.django_db
def test_bulk_guest_healing_restores_all_healable_guests_and_consumes_minimum_items(manor_with_user):
    manor, _client = manor_with_user
    template = _guest_template()
    first = Guest.objects.create(
        manor=manor,
        template=template,
        defense_stat=0,
        status=GuestStatus.INJURED,
        current_hp=1,
    )
    second = Guest.objects.create(
        manor=manor,
        template=template,
        defense_stat=0,
        status=GuestStatus.IDLE,
        current_hp=700,
    )
    _medicine(manor, heal_amount=500, quantity=3)

    result = heal_all_guests_with_medicine(manor)

    first.refresh_from_db()
    second.refresh_from_db()
    assert result == {
        "requested_count": 2,
        "healed_count": 2,
        "partial_count": 0,
        "unhealed_count": 0,
        "consumed_item_count": 3,
        "healed_hp": (first.max_hp - 1) + (second.max_hp - 700),
    }
    assert first.current_hp == first.max_hp
    assert second.current_hp == second.max_hp
    assert first.status == GuestStatus.IDLE
    assert second.status == GuestStatus.IDLE
    assert not InventoryItem.objects.filter(
        manor=manor, template__effect_type=ItemTemplate.EffectType.MEDICINE
    ).exists()


@pytest.mark.django_db
def test_bulk_guest_healing_consumes_only_the_units_actually_used(manor_with_user):
    manor, _client = manor_with_user
    template = _guest_template()
    Guest.objects.create(
        manor=manor,
        template=template,
        defense_stat=0,
        status=GuestStatus.IDLE,
        current_hp=900,
    )
    item = _medicine(manor, heal_amount=500, quantity=3)

    result = heal_all_guests_with_medicine(manor)

    item.refresh_from_db()
    assert result["consumed_item_count"] == 1
    assert result["healed_hp"] == 100
    assert item.quantity == 2


@pytest.mark.django_db
def test_bulk_guest_healing_prioritizes_injured_guest_when_stock_is_short(manor_with_user):
    manor, _client = manor_with_user
    template = _guest_template()
    injured = Guest.objects.create(
        manor=manor,
        template=template,
        defense_stat=0,
        status=GuestStatus.INJURED,
        current_hp=100,
    )
    idle_damaged = Guest.objects.create(
        manor=manor,
        template=template,
        defense_stat=0,
        status=GuestStatus.IDLE,
        current_hp=700,
    )
    _medicine(manor, heal_amount=500, quantity=1)

    result = heal_all_guests_with_medicine(manor)

    injured.refresh_from_db()
    idle_damaged.refresh_from_db()
    assert result["requested_count"] == 2
    assert result["consumed_item_count"] == 1
    assert result["partial_count"] == 1
    assert result["unhealed_count"] == 2
    assert injured.current_hp == 600
    assert injured.status == GuestStatus.IDLE
    assert idle_damaged.current_hp == 700


@pytest.mark.django_db
def test_bulk_guest_healing_does_not_consume_items_without_healable_guests(manor_with_user):
    manor, _client = manor_with_user
    template = _guest_template()
    Guest.objects.create(manor=manor, template=template, defense_stat=0, status=GuestStatus.IDLE)
    item = _medicine(manor, heal_amount=500, quantity=2)

    result = heal_all_guests_with_medicine(manor)

    item.refresh_from_db()
    assert result["requested_count"] == 0
    assert result["consumed_item_count"] == 0
    assert item.quantity == 2


@pytest.mark.django_db
def test_bulk_guest_healing_rolls_back_hp_when_item_consumption_fails(manor_with_user, monkeypatch):
    manor, _client = manor_with_user
    template = _guest_template()
    guest = Guest.objects.create(
        manor=manor,
        template=template,
        defense_stat=0,
        status=GuestStatus.INJURED,
        current_hp=100,
    )
    item = _medicine(manor, heal_amount=200, quantity=1)

    def _consume_failed(*_args, **_kwargs):
        raise RuntimeError("consume failed")

    monkeypatch.setattr("gameplay.services.inventory.core.consume_inventory_item_locked", _consume_failed)

    with pytest.raises(RuntimeError, match="consume failed"):
        heal_all_guests_with_medicine(manor)

    guest.refresh_from_db()
    item.refresh_from_db()
    assert guest.current_hp == 100
    assert guest.status == GuestStatus.INJURED
    assert item.quantity == 1


@pytest.mark.django_db
def test_bulk_guest_healing_view_reports_partial_result(manor_with_user):
    manor, client = manor_with_user
    template = _guest_template()
    Guest.objects.create(
        manor=manor,
        template=template,
        defense_stat=0,
        status=GuestStatus.INJURED,
        current_hp=100,
    )
    _medicine(manor, heal_amount=200, quantity=1)

    response = client.post(reverse("guests:heal_all_guests"))

    assert response.status_code == 302
    assert response.url == reverse("guests:roster")
    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert any("疗伤道具不足" in message for message in messages)


@pytest.mark.django_db
def test_guest_roster_places_batch_healing_button_next_to_salary_controls(manor_with_user):
    manor, client = manor_with_user
    template = _guest_template()
    Guest.objects.create(
        manor=manor,
        template=template,
        defense_stat=0,
        status=GuestStatus.INJURED,
        current_hp=100,
    )
    _medicine(manor, heal_amount=200, quantity=1)

    response = client.get(reverse("guests:roster"))

    assert response.status_code == 200
    assert 'id="heal-all-guests-form"' in response.content.decode()
    assert "一键疗伤 (1人)" in response.content.decode()
