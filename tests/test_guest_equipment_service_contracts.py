from __future__ import annotations

from types import SimpleNamespace

import pytest

from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.manor.core import ensure_manor
from guests.models import GearSlot, Guest, GuestArchetype, GuestRarity, GuestTemplate
from guests.services.equipment import (
    apply_set_bonuses,
    build_gear_template_preview,
    ensure_inventory_gears,
    resolve_equippable_gear,
)


def _build_item_template_stub(**overrides):
    payload = {
        "key": "preview_item",
        "name": "测试装备",
        "effect_type": "equip_weapon",
        "rarity": GuestRarity.GREEN,
        "effect_payload": {"force": 12},
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def test_build_gear_template_preview_rejects_non_mapping_effect_payload():
    with pytest.raises(AssertionError, match="invalid guest equipment item_template.effect_payload"):
        build_gear_template_preview(_build_item_template_stub(effect_payload=False))


def test_build_gear_template_preview_rejects_unknown_extra_stats_key():
    with pytest.raises(AssertionError, match="invalid guest equipment extra_stats key"):
        build_gear_template_preview(_build_item_template_stub(effect_payload={"mystery": 1}))


def test_build_gear_template_preview_ignores_troop_stat_bonus_metadata_for_device_items():
    preview = build_gear_template_preview(
        _build_item_template_stub(
            effect_type="equip_device",
            effect_payload={
                "force": 12,
                "troop_stat_bonus": {"gong": {"hp_flat": 20, "hp_pct": 0.5}},
            },
        )
    )

    assert preview is not None
    assert preview.slot == GearSlot.DEVICE
    assert preview.extra_stats == {"force": 12}


def test_build_gear_template_preview_rejects_invalid_set_bonus_entry():
    with pytest.raises(AssertionError, match="invalid guest equipment set_bonus\\[force\\]"):
        build_gear_template_preview(
            _build_item_template_stub(
                effect_payload={
                    "force": 12,
                    "set_bonus": {"force": "bad"},
                }
            )
        )


def test_resolve_equippable_gear_rejects_non_string_non_gear_choice():
    with pytest.raises(AssertionError, match="invalid guest equipment choice"):
        resolve_equippable_gear(SimpleNamespace(), 123)


@pytest.mark.django_db
def test_apply_set_bonuses_rejects_invalid_previous_set_bonus(django_user_model):
    user = django_user_model.objects.create_user(username="equip_contract_set_bonus", password="pass123")
    manor = ensure_manor(user)
    guest_template = GuestTemplate.objects.create(
        key="equip_contract_guest_tpl",
        name="装备契约门客",
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
def test_ensure_inventory_gears_rejects_invalid_template_payload(django_user_model):
    user = django_user_model.objects.create_user(username="equip_contract_inventory_payload", password="pass123")
    manor = ensure_manor(user)
    item_template = ItemTemplate.objects.create(
        key="equip_contract_bad_payload",
        name="坏装备模板",
        effect_type="equip_weapon",
        rarity=GuestRarity.GREEN,
        effect_payload=False,
    )
    InventoryItem.objects.create(
        manor=manor,
        template=item_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        quantity=1,
    )

    with pytest.raises(AssertionError, match="invalid guest equipment item_template.effect_payload"):
        ensure_inventory_gears(manor, slot=GearSlot.WEAPON)
