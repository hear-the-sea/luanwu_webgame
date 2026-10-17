from types import SimpleNamespace

import pytest
from django.db import transaction
from django.test import RequestFactory

from core.exceptions import DuplicateEquipmentError, EquipmentError
from gameplay.models import InventoryItem, ItemTemplate
from guests.models import GearItem, GearTemplate, Guest, GuestTemplate
from guests.services.equipment import (
    equip_guest,
    equip_guest_from_inventory_locked,
    equip_guest_from_virtual_template_locked,
    give_gear,
    unequip_guest_item,
)
from guests.services.equipment_payloads import build_gear_template_preview, same_base_equipment
from guests.views.equipment import _gear_options_for_guest


@pytest.fixture
def equipment_world(manor_factory):
    manor, user = manor_factory()
    template = GuestTemplate.objects.create(key="requirements_guest", name="门客", archetype="military")
    guest = Guest.objects.create(manor=manor, template=template, level=29)
    item = ItemTemplate.objects.create(
        key="requirements_weapon",
        name="等级武器",
        effect_type="equip_weapon",
        effect_payload={"required_level": 30, "force": 20},
    )
    gear_template = GearTemplate.objects.create(
        key=item.key,
        name=item.name,
        slot="weapon",
        required_level=30,
        extra_stats={"force": 20},
    )
    return manor, user, guest, item, gear_template


@pytest.mark.django_db
@pytest.mark.parametrize("source", ["gear", "inventory", "virtual"])
def test_level_boundary_rejects_before_consuming_and_accepts_at_requirement(equipment_world, source):
    manor, _user, guest, item, template = equipment_world
    inventory = InventoryItem.objects.create(manor=manor, template=item, quantity=1)
    gear = GearItem.objects.create(manor=manor, template=template)

    def equip():
        if source == "gear":
            return equip_guest(gear, guest)
        with transaction.atomic():
            if source == "inventory":
                return equip_guest_from_inventory_locked(manor, guest, inventory.pk)
            return equip_guest_from_virtual_template_locked(manor, guest, template.pk)

    with pytest.raises(EquipmentError, match="需要30级"):
        equip()
    inventory.refresh_from_db()
    assert inventory.quantity == 1
    assert not GearItem.objects.filter(guest=guest).exists()
    guest.level = 30
    guest.save(update_fields=["level"])
    assert equip().guest_id == guest.pk


@pytest.mark.django_db
def test_existing_equipment_exempt_until_removed(equipment_world):
    manor, _user, guest, _item, template = equipment_world
    gear = GearItem.objects.create(manor=manor, template=template, guest=guest)
    assert equip_guest(gear, guest).pk == gear.pk
    unequip_guest_item(gear, guest)
    with pytest.raises(EquipmentError, match="需要30级"):
        equip_guest(gear, guest)


def test_variant_metadata_is_not_a_stat_and_retains_identity():
    preview = build_gear_template_preview(
        SimpleNamespace(
            key="affix_force__ring",
            name="力量之玉佩",
            effect_type="equip_ornament",
            rarity="green",
            effect_payload={
                "force": 10,
                "required_level": 15,
                "equipment_base_key": "ring",
                "equipment_base_name": "玉佩",
                "equipment_affix": "force",
            },
        )
    )
    assert preview.required_level == 15
    assert preview.extra_stats == {"force": 10}
    assert same_base_equipment(preview, GearTemplate(key="ring", name="玉佩"))
    assert same_base_equipment(
        GearTemplate(key="soulorn_a", name="玉海棠"), GearTemplate(key="soulorn_b", name="玉海棠")
    )


@pytest.mark.django_db
def test_affix_cannot_bypass_duplicate_base_equipment(equipment_world):
    manor, _user, guest, _item, _template = equipment_world
    base = GearTemplate.objects.create(key="base_ring", name="玉佩", slot="ornament")
    variant = GearTemplate.objects.create(
        key="variant_ring", name="力量之玉佩", slot="ornament", base_key=base.key, base_name=base.name
    )
    equip_guest(GearItem.objects.create(manor=manor, template=base), guest)
    with pytest.raises(DuplicateEquipmentError):
        equip_guest(GearItem.objects.create(manor=manor, template=variant), guest)


@pytest.mark.django_db
def test_cached_options_calculate_level_per_guest_without_mutating_cache(equipment_world):
    manor, _user, guest, _item, _template = equipment_world
    payload = {"options": [{"id": "weapon", "required_level": 30}]}
    request = RequestFactory().get("/", {"guest": guest.pk})
    low = _gear_options_for_guest(payload, request, manor)
    assert low["options"][0]["can_equip"] is False
    assert low["options"][0]["missing_levels"] == 1
    guest.level = 30
    guest.save(update_fields=["level"])
    assert _gear_options_for_guest(payload, request, manor)["options"][0]["can_equip"] is True
    assert "can_equip" not in payload["options"][0]


@pytest.mark.django_db
def test_give_gear_rolls_once_and_preserves_binding_and_variant_identity(equipment_world, monkeypatch):
    manor, _user, _guest, item, template = equipment_world
    variant = ItemTemplate.objects.create(
        key="affix_force__requirements_weapon",
        name="力量之等级武器",
        effect_type="equip_weapon",
        effect_payload={
            "force": 20,
            "required_level": 30,
            "equipment_base_key": item.key,
            "equipment_base_name": item.name,
            "equipment_affix": "force",
        },
    )
    calls = []

    def roll(counts, *, templates):
        calls.append(counts)
        return {variant.key: 1}, {variant.key: variant}

    monkeypatch.setattr("gameplay.services.equipment_affixes.roll_equipment_reward_items_locked", roll)
    gear = give_gear(manor, template, is_bound=True)
    assert calls == [{item.key: 1}]
    assert gear.template.key == variant.key
    assert gear.template.required_level == 30
    assert gear.template.base_key == item.key
    assert gear.is_bound is True
    assert gear.inventory_backed is False


@pytest.mark.django_db
def test_give_legacy_gray_gear_without_item_template_remains_plain(equipment_world):
    manor, _user, _guest, _item, _template = equipment_world
    template = GearTemplate.objects.create(key="legacy_requirements", name="旧装备", slot="weapon")

    assert give_gear(manor, template).template_id == template.pk
    assert ItemTemplate.objects.get(key=template.key).effect_type == "equip_weapon"


@pytest.mark.django_db
def test_legacy_green_gear_roll_retains_direct_attack_and_defense(equipment_world, monkeypatch):
    manor, _user, guest, _item, _template = equipment_world
    template = GearTemplate.objects.create(
        key="legacy_green_reward",
        name="旧绿武器",
        slot="weapon",
        rarity="green",
        attack_bonus=11,
        defense_bonus=7,
        extra_stats={"force": 20},
        required_level=20,
    )
    monkeypatch.setattr("gameplay.services.equipment_affixes.binomial_sample", lambda n, p, rng: n)
    gear = give_gear(manor, template, is_bound=True)
    assert gear.template.name == "敏捷之旧绿武器"
    assert gear.template.extra_stats == {"force": 16, "agility": 4}
    assert gear.template.attack_bonus == 11
    assert gear.template.defense_bonus == 7
    assert gear.template.required_level == 20
    assert gear.template.base_key == template.key
    before_attack, before_defense = guest.attack_bonus, guest.defense_bonus
    equip_guest(gear, guest)
    guest.refresh_from_db()
    assert guest.attack_bonus == before_attack + 11
    assert guest.defense_bonus == before_defense + 7
    unequip_guest_item(gear, guest)
    inventory = InventoryItem.objects.get(manor=manor, template__key=gear.template.key)
    assert inventory.bound_quantity == 1
    preview = build_gear_template_preview(inventory.template)
    assert preview.attack_bonus == 11
    assert preview.defense_bonus == 7
