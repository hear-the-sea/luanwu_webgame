from __future__ import annotations

from types import SimpleNamespace

import pytest

from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.manor.core import ensure_manor
from guests.models import GearItem, GearSlot, GearTemplate, Guest, GuestArchetype, GuestRarity, GuestTemplate
from guests.services.equipment_inventory import list_available_equippable_gear_options
from guests.services.equipment_payloads import build_gear_template_preview
from guests.services.equipment_stats import apply_set_bonuses


def _build_item_template_stub(**overrides):
    payload = {
        "key": "split_preview_item",
        "name": "拆分测试装备",
        "effect_type": "equip_weapon",
        "rarity": GuestRarity.GREEN,
        "effect_payload": {"force": 12},
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def test_payload_module_builds_preview_with_nested_set_bonus():
    preview = build_gear_template_preview(
        _build_item_template_stub(
            effect_payload={
                "force": 12,
                "set_key": "split_set",
                "set_description": "拆分测试套装",
                "set_bonus": {
                    "pieces": 4,
                    "bonus": {
                        "force": 8,
                    },
                },
            }
        )
    )

    assert preview is not None
    assert preview.slot == GearSlot.WEAPON
    assert preview.set_bonus == {"pieces": 4, "bonus": {"force": 8}}


def test_payload_module_builds_preview_with_multi_tier_set_bonus():
    set_bonus = [
        {"pieces": 2, "bonus": {"attack": 40, "defense": 30}},
        {"pieces": 4, "bonus": {"hp": 600, "agility": 25, "luck": 20}},
    ]

    preview = build_gear_template_preview(
        _build_item_template_stub(
            effect_payload={
                "force": 12,
                "set_key": "multi_tier_split_set",
                "set_description": "多档测试套装",
                "set_bonus": set_bonus,
            }
        )
    )

    assert preview is not None
    assert preview.slot == GearSlot.WEAPON
    assert preview.set_bonus == set_bonus


@pytest.mark.django_db
def test_inventory_module_lists_equippable_options_without_materializing_gear(django_user_model):
    user = django_user_model.objects.create_user(username="split_inventory_options", password="pass123")
    manor = ensure_manor(user)
    template = ItemTemplate.objects.create(
        key="split_inventory_weapon",
        name="拆分测试刀",
        effect_type="equip_weapon",
        rarity=GuestRarity.GREEN,
        effect_payload={"force": 7},
    )
    InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=2,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    options = list_available_equippable_gear_options(manor, slot=GearSlot.WEAPON)

    assert len(options) == 1
    assert options[0]["template_key"] == template.key
    assert options[0]["count"] == 2
    assert not GearItem.objects.filter(manor=manor, template__key=template.key).exists()


@pytest.mark.django_db
def test_stats_module_apply_set_bonuses_rejects_invalid_previous_payload(django_user_model):
    user = django_user_model.objects.create_user(username="split_stats_payload", password="pass123")
    manor = ensure_manor(user)
    guest_template = GuestTemplate.objects.create(
        key="split_stats_guest_tpl",
        name="拆分测试门客",
        archetype=GuestArchetype.CIVIL,
        rarity=GuestRarity.GRAY,
    )
    guest = Guest.objects.create(
        manor=manor,
        template=guest_template,
        gear_set_bonus={"force": "bad"},
        status="idle",
    )

    with pytest.raises(AssertionError, match="invalid guest equipment set_bonus\\[force\\]"):
        apply_set_bonuses(guest)


@pytest.mark.django_db
def test_stats_module_apply_set_bonuses_accumulates_multi_tier_set_bonus(django_user_model):
    user = django_user_model.objects.create_user(username="split_stats_multi_tier_set", password="pass123")
    manor = ensure_manor(user)
    guest_template = GuestTemplate.objects.create(
        key="split_stats_multi_tier_guest_tpl",
        name="多档套装门客",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.BLUE,
    )
    guest = Guest.objects.create(manor=manor, template=guest_template, status="idle")
    set_bonus = [
        {"pieces": 2, "bonus": {"attack": 40, "defense": 30}},
        {"pieces": 4, "bonus": {"hp": 600, "agility": 25, "luck": 20}},
    ]
    for index in range(4):
        template = GearTemplate.objects.create(
            key=f"split_stats_multi_tier_gear_{index}",
            name=f"多档套装装备{index}",
            slot=GearSlot.DEVICE,
            rarity=GuestRarity.BLUE,
            set_key="multi_tier_test_set",
            set_bonus=set_bonus,
        )
        GearItem.objects.create(manor=manor, template=template, guest=guest)

    result = apply_set_bonuses(guest)

    guest.refresh_from_db()
    assert result == {"attack": 40, "defense": 30, "hp": 600, "agility": 25, "luck": 20}
    assert guest.attack_bonus == 40
    assert guest.defense_bonus == 30
    assert guest.hp_bonus == 600
    assert guest.agility == 105
    assert guest.luck == 70
