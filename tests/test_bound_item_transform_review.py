from types import SimpleNamespace

import pytest
from django.db import transaction
from django.template.loader import render_to_string

from core.exceptions import ItemResourceOverflowConfirmationRequired
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.inventory.core import set_warehouse_grain_quantity_locked
from gameplay.services.inventory.use import use_inventory_item
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestTemplate
from guests.services.equipment import equip_guest_from_inventory_locked, unequip_guest_item

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("effect_type", ["resource_pack", "loot_box"])
@pytest.mark.parametrize("bound_source", [True, False])
def test_resource_reward_grain_inherits_binding_without_changing_existing_stock(
    django_user_model,
    effect_type,
    bound_source,
):
    manor = ensure_manor(django_user_model.objects.create_user(username="bound_grain_reward"))
    grain, _ = ItemTemplate.objects.get_or_create(key="grain", defaults={"name": "粮食"})
    with transaction.atomic():
        set_warehouse_grain_quantity_locked(manor, 7, grain_template=grain)
    payload = {"grain": 10} if effect_type == "resource_pack" else {"resources": {"grain": 10}}
    template = ItemTemplate.objects.create(
        key="binding_grain_reward_pack",
        name="粮食包",
        effect_type=effect_type,
        effect_payload=payload,
        is_usable=True,
    )
    source = InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=2,
        bound_quantity=int(bound_source),
    )
    use_inventory_item(source, manor)
    result = InventoryItem.objects.get(manor=manor, template=grain)
    manor.refresh_from_db()
    assert (result.quantity, result.bound_quantity, manor.grain) == (17, 10 if bound_source else 0, 17)
    source.refresh_from_db()
    assert (source.quantity, source.bound_quantity) == (1, 0)


def test_bound_grain_resource_pack_binds_only_reward_within_capacity(django_user_model, monkeypatch):
    from gameplay.services import resources

    manor = ensure_manor(django_user_model.objects.create_user(username="bound_grain_overflow"))
    grain, _ = ItemTemplate.objects.get_or_create(key="grain", defaults={"name": "粮食"})
    with transaction.atomic():
        set_warehouse_grain_quantity_locked(manor, 7, grain_template=grain)
    original_capacity = resources._get_resource_capacity
    monkeypatch.setattr(
        resources,
        "_get_resource_capacity",
        lambda current, resource: (10, True) if resource == "grain" else original_capacity(current, resource),
    )
    template = ItemTemplate.objects.create(
        key="binding_grain_overflow_pack",
        name="绑定粮食包",
        effect_type="resource_pack",
        effect_payload={"grain": 10},
        is_usable=True,
    )
    source = InventoryItem.objects.create(manor=manor, template=template, quantity=1, bound_quantity=1)
    with pytest.raises(ItemResourceOverflowConfirmationRequired) as requested:
        use_inventory_item(source, manor)
    use_inventory_item(source, manor, resource_overflow_confirmation=requested.value.confirmation_snapshot)
    result = InventoryItem.objects.get(manor=manor, template=grain)
    assert (result.quantity, result.bound_quantity) == (10, 3)


def test_inventory_id_equipment_entry_and_detail_keep_bound_state(django_user_model):
    manor = ensure_manor(django_user_model.objects.create_user(username="bound_locked_equipment"))
    guest_template = GuestTemplate.objects.create(key="bound_locked_guest", name="测试门客", base_hp=1000)
    guest = Guest.objects.create(manor=manor, template=guest_template)
    template = ItemTemplate.objects.create(key="bound_locked_weapon", name="绑定长剑", effect_type="equip_weapon")
    source = InventoryItem.objects.create(manor=manor, template=template, quantity=1, bound_quantity=1)
    with transaction.atomic():
        gear = equip_guest_from_inventory_locked(manor, guest, source.id)
    assert gear.is_bound is True
    html = render_to_string(
        "guests/partials/detail_equipment_card.html",
        {
            "guest": guest,
            "slot_panels_map": {"weapon": SimpleNamespace(equipped=[gear])},
            "gear_set_map": {},
        },
    )
    assert "绑定长剑" in html
    assert "（绑定）" in html
    unequip_guest_item(gear, guest)
    returned = InventoryItem.objects.get(manor=manor, template=template)
    assert (returned.quantity, returned.bound_quantity) == (1, 1)


@pytest.mark.parametrize("bound_source", [True, False])
def test_legacy_gear_only_loot_reward_preserves_binding(django_user_model, bound_source):
    from guests.models import GearItem, GearTemplate

    manor = ensure_manor(django_user_model.objects.create_user(username="bound_legacy_gear"))
    gear_template = GearTemplate.objects.create(key="legacy_binding_weapon", name="旧式佩剑", slot="weapon")
    template = ItemTemplate.objects.create(
        key="legacy_binding_box",
        name="旧式宝箱",
        effect_type="loot_box",
        is_usable=True,
        effect_payload={"gear_chance": 1, "gear_keys": [gear_template.key]},
    )
    source = InventoryItem.objects.create(manor=manor, template=template, quantity=1, bound_quantity=int(bound_source))
    use_inventory_item(source, manor)
    reward = GearItem.objects.get(manor=manor, template=gear_template)
    assert reward.is_bound is bound_source


@pytest.mark.parametrize("bound_source", [True, False])
def test_soul_fusion_generated_ornament_inherits_binding(django_user_model, monkeypatch, bound_source):
    from gameplay.services.inventory.guest_items import use_soul_container
    from tests.inventory_guest_items.support_upgrade import _prepare_soul_container_case

    manor, guest, source = _prepare_soul_container_case(
        django_user_model,
        "binding_review",
        guest_rarity="green",
        archetype="military",
        level=30,
        force=180,
        intellect=120,
        defense=150,
        agility=140,
        luck=80,
    )
    source.bound_quantity = int(bound_source)
    source.save(update_fields=["bound_quantity"])
    ornament_template = ItemTemplate.objects.create(
        key="binding_soul_ornament", name="灵魂饰品", effect_type="equip_ornament"
    )
    monkeypatch.setattr(
        "gameplay.services.inventory.guest_items._create_soul_fusion_ornament_template",
        lambda *_args: ornament_template,
    )
    result = use_soul_container(manor, source, guest.id)
    reward = InventoryItem.objects.get(pk=result["generated_item_id"])
    assert reward.bound_quantity == int(bound_source)
    assert not Guest.objects.filter(pk=guest.pk).exists()
