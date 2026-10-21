from __future__ import annotations

from uuid import uuid4

import pytest

from core.exceptions import ItemNotConfiguredError, ItemNotUsableError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.inventory.use import use_inventory_item
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestStatus, GuestTemplate

pytestmark = pytest.mark.django_db


def _item(manor, *, hp=5000):
    template = ItemTemplate.objects.create(
        key=f"manor_plum_{uuid4().hex[:12]}",
        name="李子",
        rarity="purple",
        effect_type="tool",
        is_usable=True,
        effect_payload={"action": "heal_all_guests", "hp": hp},
    )
    return InventoryItem.objects.create(manor=manor, template=template, quantity=2, bound_quantity=1)


def _guest(manor, *, hp=100, base_hp=10000, status=GuestStatus.IDLE, **kwargs):
    template = GuestTemplate.objects.create(
        key=f"manor_patient_{uuid4().hex[:12]}", name="受伤门客", archetype="civil", rarity="gray", base_hp=base_hp
    )
    return Guest.objects.create(manor=manor, template=template, defense_stat=0, current_hp=hp, status=status, **kwargs)


def test_plum_heals_every_status_once_preserves_busy_state_and_other_manors(manor_with_user, django_user_model):
    manor, _ = manor_with_user
    guests = [_guest(manor, status=status, training_remaining_seconds=90) for status in GuestStatus.values]
    capped = _guest(manor, hp=9900)
    foreign_manor = ensure_manor(django_user_model.objects.create_user(username="foreign_plum_manor"))
    foreign = _guest(foreign_manor)
    item = _item(manor)

    result = use_inventory_item(item, manor, binding_mode="bound")

    for guest in guests:
        original_status = guest.status
        guest.refresh_from_db()
        assert guest.current_hp == 5100
        assert guest.status == (GuestStatus.IDLE if original_status == GuestStatus.INJURED else original_status)
        if original_status not in {GuestStatus.IDLE, GuestStatus.INJURED}:
            assert guest.training_remaining_seconds == 90
            assert guest.training_complete_at is None
    capped.refresh_from_db()
    foreign.refresh_from_db()
    item.refresh_from_db()
    assert capped.current_hp == capped.max_hp == 10000
    assert foreign.current_hp == 100
    assert (item.quantity, item.bound_quantity) == (1, 0)
    assert result["healed_count"] == len(guests) + 1
    assert result["healed_hp"] == len(guests) * 5000 + 100


@pytest.mark.parametrize("base_hp,cured", [(30000, False), (20000, True)])
def test_plum_uses_existing_injury_threshold_and_training_resume(manor_with_user, monkeypatch, base_hp, cured):
    manor, _ = manor_with_user
    guest = _guest(manor, base_hp=base_hp, status=GuestStatus.INJURED, training_remaining_seconds=90)
    calls = []
    monkeypatch.setattr(
        "guests.services.health.schedule_resumed_guest_training", lambda guest, **kw: calls.append(guest.pk)
    )

    result = use_inventory_item(_item(manor), manor)

    guest.refresh_from_db()
    assert guest.current_hp == 5100
    assert result["injury_cured_count"] == int(cured)
    assert guest.status == (GuestStatus.IDLE if cured else GuestStatus.INJURED)
    assert (guest.training_complete_at is not None) == cured
    assert guest.training_remaining_seconds == (None if cured else 90)
    assert calls == ([guest.pk] if cured else [])


@pytest.mark.parametrize("has_full_guest", [False, True])
def test_plum_does_not_consume_when_no_one_needs_healing(manor_with_user, has_full_guest):
    manor, _ = manor_with_user
    if has_full_guest:
        _guest(manor, hp=10000)
    item = _item(manor)
    with pytest.raises(ItemNotUsableError):
        use_inventory_item(item, manor)
    item.refresh_from_db()
    assert (item.quantity, item.bound_quantity) == (2, 1)


@pytest.mark.parametrize("failure_stage", ["second_guest", "consume"])
def test_plum_rolls_back_every_guest_and_inventory_on_failure(manor_with_user, monkeypatch, failure_stage):
    manor, _ = manor_with_user
    guests = [_guest(manor), _guest(manor)]
    item = _item(manor)
    if failure_stage == "second_guest":
        from gameplay.services.inventory import manor_healing

        original = manor_healing.heal_guest

        def fail_second(guest, *args, **kwargs):
            if guest.pk == guests[1].pk:
                raise RuntimeError("healing failure")
            return original(guest, *args, **kwargs)

        monkeypatch.setattr(manor_healing, "heal_guest", fail_second)
    else:

        def fail_consume(*args, **kwargs):
            raise RuntimeError("healing failure")

        monkeypatch.setattr("gameplay.services.inventory.use.consume_inventory_item_locked", fail_consume)
    with pytest.raises(RuntimeError, match="healing failure"):
        use_inventory_item(item, manor)
    for guest in guests:
        guest.refresh_from_db()
        assert guest.current_hp == 100
    item.refresh_from_db()
    assert (item.quantity, item.bound_quantity) == (2, 1)


@pytest.mark.parametrize("hp", [True, 0, -1, None, "5000", 5000.5])
def test_plum_rejects_invalid_healing_configuration_without_consuming(manor_with_user, hp):
    manor, _ = manor_with_user
    guest = _guest(manor)
    item = _item(manor, hp=hp)
    with pytest.raises(ItemNotConfiguredError):
        use_inventory_item(item, manor)
    guest.refresh_from_db()
    item.refresh_from_db()
    assert guest.current_hp == 100
    assert item.quantity == 2
