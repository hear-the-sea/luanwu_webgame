from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from core.exceptions import PeaceShieldUnavailableError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.inventory.use import use_inventory_item
from gameplay.services.manor.core import ensure_manor
from gameplay.services.raid.protection import activate_peace_shield
from guests.models import Guest, GuestStatus, GuestTemplate
from guests.services.world_unique import WORLD_UNIQUE_LUBU_TEMPLATE_KEY


@pytest.mark.django_db
def test_activate_peace_shield_rejects_when_active_raids_exist(monkeypatch):
    user = get_user_model().objects.create_user(username="raid_protect_active", password="pass123")
    manor = ensure_manor(user)

    monkeypatch.setattr("gameplay.services.raid.protection.get_active_raid_count", lambda *_a, **_k: 1)
    monkeypatch.setattr("gameplay.services.raid.protection.get_incoming_raids", lambda *_a, **_k: [])

    with pytest.raises(PeaceShieldUnavailableError, match="出征中的队伍"):
        activate_peace_shield(manor, 3600)


@pytest.mark.django_db
def test_activate_peace_shield_rejects_when_incoming_raids_exist(monkeypatch):
    user = get_user_model().objects.create_user(username="raid_protect_incoming", password="pass123")
    manor = ensure_manor(user)

    monkeypatch.setattr("gameplay.services.raid.protection.get_active_raid_count", lambda *_a, **_k: 0)
    monkeypatch.setattr("gameplay.services.raid.protection.get_incoming_raids", lambda *_a, **_k: [object()])

    with pytest.raises(PeaceShieldUnavailableError, match="敌军来袭"):
        activate_peace_shield(manor, 3600)


@pytest.mark.django_db
def test_activate_peace_shield_extends_existing_duration(monkeypatch):
    user = get_user_model().objects.create_user(username="raid_protect_extend", password="pass123")
    manor = ensure_manor(user)

    now = timezone.now()
    manor.peace_shield_until = now + timedelta(hours=2)
    manor.save(update_fields=["peace_shield_until"])

    monkeypatch.setattr("gameplay.services.raid.protection.get_active_raid_count", lambda *_a, **_k: 0)
    monkeypatch.setattr("gameplay.services.raid.protection.get_incoming_raids", lambda *_a, **_k: [])

    activate_peace_shield(manor, 3600)

    manor.refresh_from_db(fields=["peace_shield_until"])
    expected = now + timedelta(hours=3)
    assert abs((manor.peace_shield_until - expected).total_seconds()) <= 1


@pytest.mark.django_db
@pytest.mark.parametrize("status", [GuestStatus.IDLE, GuestStatus.VACATION, GuestStatus.DEPLOYED])
@pytest.mark.parametrize("legacy_lubu", [False, True])
def test_unique_guest_blocks_shield_in_any_status(manor_factory, status, legacy_lubu):
    manor, _user = manor_factory(username="unique_shield_owner")
    key = WORLD_UNIQUE_LUBU_TEMPLATE_KEY if legacy_lubu else "shield_unique_guest"
    template, _ = GuestTemplate.objects.update_or_create(
        key=key,
        defaults={"name": "唯一门客", "archetype": "military", "rarity": "orange", "is_world_unique": not legacy_lubu},
    )
    Guest.objects.create(manor=manor, template=template, status=status)
    original_until = timezone.now() + timedelta(hours=2)
    manor.peace_shield_until = original_until
    manor.save(update_fields=["peace_shield_until"])

    with pytest.raises(PeaceShieldUnavailableError, match="拥有唯一门客"):
        activate_peace_shield(manor, 3600)

    manor.refresh_from_db()
    assert manor.peace_shield_until == original_until


@pytest.mark.django_db
@pytest.mark.parametrize("size,duration", [("small", 28800), ("medium", 86400), ("large", 259200)])
def test_rejected_shield_does_not_consume_inventory(manor_factory, size, duration):
    manor, _user = manor_factory(username="unique_shield_inventory")
    template = GuestTemplate.objects.create(
        key="shield_inventory_unique", name="唯一门客", archetype="military", rarity="orange", is_world_unique=True
    )
    Guest.objects.create(manor=manor, template=template)
    item_template, _ = ItemTemplate.objects.update_or_create(
        key=f"peace_shield_{size}",
        defaults={"name": "免战牌", "effect_type": "tool", "is_usable": True, "effect_payload": {"duration": duration}},
    )
    item = InventoryItem.objects.create(manor=manor, template=item_template, quantity=2, bound_quantity=1)
    original_until = manor.peace_shield_until

    with pytest.raises(PeaceShieldUnavailableError, match="拥有唯一门客"):
        use_inventory_item(item, manor=manor)

    item.refresh_from_db()
    manor.refresh_from_db()
    assert item.quantity == 2
    assert item.bound_quantity == 1
    assert manor.peace_shield_until == original_until


@pytest.mark.django_db
def test_other_manors_unique_guest_does_not_block_shield(manor_factory, monkeypatch):
    manor, _user = manor_factory(username="ordinary_shield_owner")
    other, _other_user = manor_factory(username="other_unique_shield_owner")
    unique = GuestTemplate.objects.create(
        key="other_shield_unique", name="唯一门客", archetype="military", rarity="orange", is_world_unique=True
    )
    Guest.objects.create(manor=other, template=unique)
    ordinary = GuestTemplate.objects.create(
        key="ordinary_shield_guest", name="普通门客", archetype="military", rarity="orange"
    )
    Guest.objects.create(manor=manor, template=ordinary)
    monkeypatch.setattr("gameplay.services.raid.protection.get_active_raid_count", lambda *_a, **_k: 0)
    monkeypatch.setattr("gameplay.services.raid.protection.get_incoming_raids", lambda *_a, **_k: [])

    activate_peace_shield(manor, 3600)

    manor.refresh_from_db()
    assert manor.peace_shield_until > timezone.now()
