from __future__ import annotations

import random
from types import SimpleNamespace
from uuid import uuid4

import pytest

from core.exceptions import GuestAlreadyOwnedError, WorldUniqueGuestError
from gameplay.models import InventoryItem, ItemTemplate, OathBond
from gameplay.selectors.warehouse import get_warehouse_context
from gameplay.services.inventory.use import use_inventory_item
from gameplay.services.jail import add_oath_bond
from gameplay.services.raid.combat import capture as capture_service
from guests.models import GearItem, GearSlot, GearTemplate, Guest, GuestTemplate, WorldUniqueGuest
from guests.services.recruitment_guests import create_guest_from_template
from guests.services.world_unique import (
    WORLD_UNIQUE_LUBU_SCROLL_ITEM_KEY,
    WORLD_UNIQUE_LUBU_TEMPLATE_KEY,
    _status_payload_from_state,
    claim_world_unique_guest_from_scroll,
    get_world_unique_guest_status,
    release_world_unique_guest_after_raid,
)


def _key(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}"


def _lubu_template() -> GuestTemplate:
    template, _created = GuestTemplate.objects.get_or_create(
        key=WORLD_UNIQUE_LUBU_TEMPLATE_KEY,
        defaults={
            "name": "吕布",
            "archetype": "military",
            "rarity": "purple",
            "base_attack": 150,
            "base_intellect": 80,
            "base_defense": 110,
            "base_agility": 100,
            "base_luck": 50,
            "base_hp": 1400,
        },
    )
    if not template.is_world_unique or template.recruitable:
        template.is_world_unique = True
        template.recruitable = False
        template.save(update_fields=["is_world_unique", "recruitable"])
    state, _created = WorldUniqueGuest.objects.get_or_create(template=template)
    state.status = WorldUniqueGuest.Status.WILD
    state.owner_manor = None
    state.owner_guest = None
    state.save(update_fields=["status", "owner_manor", "owner_guest", "updated_at"])
    return template


def _scroll(manor, *, quantity: int = 1) -> InventoryItem:
    template, _created = ItemTemplate.objects.update_or_create(
        key=WORLD_UNIQUE_LUBU_SCROLL_ITEM_KEY,
        defaults={
            "name": "吕布召唤卷轴",
            "effect_type": ItemTemplate.EffectType.TOOL,
            "effect_payload": {
                "action": "summon_guest",
                "choices": [{"template_key": WORLD_UNIQUE_LUBU_TEMPLATE_KEY, "weight": 1}],
                "exclusive_template_keys": [WORLD_UNIQUE_LUBU_TEMPLATE_KEY],
            },
            "is_usable": True,
        },
    )
    return InventoryItem.objects.create(
        manor=manor,
        template=template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        quantity=quantity,
    )


def _claim(manor) -> Guest:
    return claim_world_unique_guest_from_scroll(
        manor,
        WORLD_UNIQUE_LUBU_SCROLL_ITEM_KEY,
        _lubu_template(),
        rng=random.Random(1),
    )


@pytest.mark.django_db
def test_lubu_scroll_claim_is_global_and_shows_holder_status(manor_factory):
    owner, _user = manor_factory(username=_key("world_unique_owner"))
    owner.name = "吕布持有者"
    owner.coordinate_x = 11
    owner.coordinate_y = 22
    owner.save(update_fields=["name", "coordinate_x", "coordinate_y"])
    _lubu_template()

    scroll = _scroll(owner, quantity=2)
    status_before = get_world_unique_guest_status()
    assert status_before["summary"] == "在野"

    effect = use_inventory_item(scroll, manor=owner)
    guest = Guest.objects.get(manor=owner, template__key=WORLD_UNIQUE_LUBU_TEMPLATE_KEY)
    status_after = get_world_unique_guest_status()
    assert guest.template.key == WORLD_UNIQUE_LUBU_TEMPLATE_KEY
    assert effect["全服唯一"] is True
    assert status_after["owner_manor_id"] == owner.pk
    assert "吕布持有者" in status_after["summary"]
    assert "(11, 22)" in status_after["summary"]
    assert (
        not InventoryItem.objects.filter(pk=scroll.pk).exists() or InventoryItem.objects.get(pk=scroll.pk).quantity == 1
    )

    other, _other_user = manor_factory(username=_key("world_unique_other"))
    other_scroll = _scroll(other)
    with pytest.raises(GuestAlreadyOwnedError, match="吕布持有者"):
        use_inventory_item(other_scroll, manor=other)
    assert InventoryItem.objects.get(pk=other_scroll.pk).quantity == 1


@pytest.mark.django_db
def test_lubu_status_is_only_attached_to_scroll_use_confirmation(manor_factory):
    owner, _user = manor_factory(username=_key("world_unique_warehouse"))
    _lubu_template()
    scroll = _scroll(owner)

    context = get_warehouse_context(owner, current_tab="warehouse", selected_category="all", page=1)

    entry = next(item for item in context["inventory_items"] if item.pk == scroll.pk)
    assert not hasattr(entry, "world_unique_status_summary")
    assert entry.world_unique_use_guest_name == "吕布"
    assert entry.world_unique_use_status_summary == "在野"


@pytest.mark.django_db
def test_malformed_serving_status_is_not_reported_as_available():
    template = _lubu_template()
    malformed_state = SimpleNamespace(
        status=WorldUniqueGuest.Status.SERVING,
        owner_manor_id=None,
        owner_guest_id=None,
        owner_manor=None,
        template=template,
    )

    status = _status_payload_from_state(malformed_state)

    assert status["summary"] == "仕官（状态异常）"
    assert status["is_available"] is False


@pytest.mark.django_db
def test_normal_guest_creation_and_oath_bond_reject_lubu(manor_factory):
    manor, _user = manor_factory(username=_key("world_unique_guard"))
    template = _lubu_template()

    with pytest.raises(WorldUniqueGuestError, match="专属召唤卷轴"):
        create_guest_from_template(manor=manor, template=template)

    guest = _claim(manor)
    with pytest.raises(WorldUniqueGuestError, match="不可结义"):
        add_oath_bond(manor, guest.pk)


@pytest.mark.django_db
def test_lubu_gear_returns_to_original_warehouse_when_lost(manor_factory):
    manor, _user = manor_factory(username=_key("world_unique_gear"))
    guest = _claim(manor)
    gear_template = GearTemplate.objects.create(
        key=_key("world_unique_gear_template"),
        name="吕布旧装备",
        slot=GearSlot.WEAPON,
    )
    item_template = ItemTemplate.objects.create(
        key=gear_template.key,
        name="吕布旧装备",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
    )
    gear = GearItem.objects.create(manor=manor, template=gear_template, guest=guest)
    OathBond.objects.create(manor=manor, guest=guest)

    result = release_world_unique_guest_after_raid(
        manor,
        guest_id=guest.pk,
        losing_side="defender",
    )

    assert result == {
        "guest_name": "吕布",
        "template_key": WORLD_UNIQUE_LUBU_TEMPLATE_KEY,
        "from": "defender",
        "returned_gear_count": 1,
        "into": WorldUniqueGuest.Status.WILD,
    }
    assert not Guest.objects.filter(pk=guest.pk).exists()
    gear.refresh_from_db()
    assert gear.guest_id is None
    assert gear.inventory_backed is True
    assert (
        InventoryItem.objects.get(
            manor=manor,
            template=item_template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        ).quantity
        == 1
    )
    assert not OathBond.objects.filter(guest_id=guest.pk).exists()
    assert get_world_unique_guest_status()["summary"] == "在野"

    regenerated_scroll = _scroll(manor)
    regenerated_effect = use_inventory_item(regenerated_scroll, manor=manor)
    regenerated_guest = Guest.objects.get(manor=manor, template__key=WORLD_UNIQUE_LUBU_TEMPLATE_KEY)
    assert regenerated_guest.pk != guest.pk
    assert regenerated_effect["全服唯一"] is True
    assert get_world_unique_guest_status()["owner_manor_id"] == manor.pk


@pytest.mark.django_db
@pytest.mark.parametrize("owner_is_attacker", [True, False])
def test_only_losing_participating_lubu_returns_to_wild(manor_factory, owner_is_attacker):
    owner, _user = manor_factory(username=_key("world_unique_battle_owner"))
    opponent, _opponent_user = manor_factory(username=_key("world_unique_battle_opponent"))
    guest = _claim(owner)
    run = SimpleNamespace(
        attacker=owner if owner_is_attacker else opponent,
        defender=opponent if owner_is_attacker else owner,
    )
    is_attacker_victory = not owner_is_attacker
    report = SimpleNamespace(
        winner="defender" if owner_is_attacker else "attacker",
        attacker_team=(
            [{"guest_id": guest.pk, "template_key": WORLD_UNIQUE_LUBU_TEMPLATE_KEY}] if owner_is_attacker else []
        ),
        defender_team=(
            [{"guest_id": guest.pk, "template_key": WORLD_UNIQUE_LUBU_TEMPLATE_KEY}] if not owner_is_attacker else []
        ),
    )

    result = capture_service._release_losing_world_unique_guest(run, report, is_attacker_victory)

    assert result is not None
    assert result["from"] == ("attacker" if owner_is_attacker else "defender")
    assert get_world_unique_guest_status()["summary"] == "在野"
    assert not Guest.objects.filter(pk=guest.pk).exists()


@pytest.mark.django_db
def test_lubu_loss_requires_a_winning_side_and_actual_participation(manor_factory):
    owner, _user = manor_factory(username=_key("world_unique_no_loss_owner"))
    opponent, _opponent_user = manor_factory(username=_key("world_unique_no_loss_opponent"))
    guest = _claim(owner)
    run = SimpleNamespace(attacker=opponent, defender=owner)

    no_participation_report = SimpleNamespace(winner="attacker", attacker_team=[], defender_team=[])
    assert capture_service._release_losing_world_unique_guest(run, no_participation_report, True) is None

    draw_report = SimpleNamespace(
        winner="draw",
        attacker_team=[],
        defender_team=[{"guest_id": guest.pk, "template_key": WORLD_UNIQUE_LUBU_TEMPLATE_KEY}],
    )
    assert capture_service._release_losing_world_unique_guest(run, draw_report, False) is None
    assert Guest.objects.filter(pk=guest.pk).exists()
    assert get_world_unique_guest_status()["owner_manor_id"] == owner.pk


@pytest.mark.django_db
def test_lubu_is_never_selected_by_normal_capture(manor_factory, monkeypatch):
    owner, _user = manor_factory(username=_key("world_unique_capture_owner"))
    opponent, _opponent_user = manor_factory(username=_key("world_unique_capture_opponent"))
    guest = _claim(owner)
    run = SimpleNamespace(attacker=opponent, defender=owner)
    report = SimpleNamespace(
        winner="attacker",
        defender_team=[{"guest_id": guest.pk, "template_key": WORLD_UNIQUE_LUBU_TEMPLATE_KEY}],
        attacker_team=[],
    )
    monkeypatch.setattr(capture_service, "_can_attempt_capture", lambda *_args, **_kwargs: True)

    result = capture_service._try_capture_guest(run, report, True, rng=random.Random(1))

    assert result is None
    assert Guest.objects.filter(pk=guest.pk).exists()
    assert not OathBond.objects.filter(guest_id=guest.pk).exists()
