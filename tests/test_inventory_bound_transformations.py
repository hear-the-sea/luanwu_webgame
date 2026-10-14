from __future__ import annotations

import pytest
from django.db import transaction
from django.utils import timezone

from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.buildings import forge as forge_service
from gameplay.services.inventory.use import use_inventory_item
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestTemplate
from guests.services.equipment import equip_guest, return_guest_gear_to_inventory_locked, unequip_guest_item

pytestmark = pytest.mark.django_db


def _manor(django_user_model):
    return ensure_manor(django_user_model.objects.create_user(username="bound_transformation", password="test"))


def _item(manor, key, *, quantity=1, bound_quantity=0, effect_type="tool", payload=None, usable=False):
    template = ItemTemplate.objects.create(
        key=key,
        name=key,
        rarity="green",
        effect_type=effect_type,
        effect_payload=payload or {},
        is_usable=usable,
    )
    return InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=quantity,
        bound_quantity=bound_quantity,
    )


@pytest.mark.parametrize("return_method", ["unequip", "replace", "dismiss"])
def test_equipment_binding_survives_every_return_path(django_user_model, return_method):
    manor = _manor(django_user_model)
    guest_template = GuestTemplate.objects.create(key="bound_guest", name="绑定装备门客", base_hp=1000)
    guest = Guest.objects.create(manor=manor, template=guest_template, force=100, defense_stat=100)
    weapon = _item(
        manor, "bound_weapon", quantity=2, bound_quantity=1, effect_type="equip_weapon", payload={"force": 5}
    )
    gear = equip_guest(weapon.template.key, guest)
    assert gear.is_bound is True
    weapon.refresh_from_db()
    assert (weapon.quantity, weapon.bound_quantity) == (1, 0)

    if return_method == "unequip":
        unequip_guest_item(gear, guest)
    elif return_method == "replace":
        replacement = _item(manor, "replacement_weapon", effect_type="equip_weapon", payload={"force": 10})
        new_gear = equip_guest(replacement.template.key, guest)
        assert new_gear.is_bound is False
    else:
        with transaction.atomic():
            assert return_guest_gear_to_inventory_locked(manor, guest) == 1
    weapon.refresh_from_db()
    assert (weapon.quantity, weapon.bound_quantity) == (2, 1)
    gear.refresh_from_db()
    assert gear.is_bound is True
    assert gear.guest_id is None


@pytest.mark.parametrize("bound_source,bound_material", [(True, False), (False, True), (False, False)])
def test_synthesis_inherits_source_or_material_binding(django_user_model, bound_source, bound_material):
    manor = _manor(django_user_model)
    material = _item(manor, "bound_material", quantity=5, bound_quantity=int(bound_material))
    result_template = ItemTemplate.objects.create(key="bound_result", name="合成产物", effect_type="tool")
    blueprint = _item(
        manor,
        "bound_blueprint",
        bound_quantity=int(bound_source),
        usable=True,
        payload={
            "action": "synthesize_item",
            "required_items": {material.template.key: 3},
            "result_item_key": result_template.key,
            "result_quantity": 2,
        },
    )
    use_inventory_item(blueprint, manor)
    output = InventoryItem.objects.get(manor=manor, template=result_template)
    assert (output.quantity, output.bound_quantity) == (2, 2 if bound_source or bound_material else 0)
    material.refresh_from_db()
    assert (material.quantity, material.bound_quantity) == (2, 0)


@pytest.mark.parametrize("bound_source", [True, False])
def test_loot_box_binding_covers_items_gear_books_and_blueprint_support(django_user_model, monkeypatch, bound_source):
    manor = _manor(django_user_model)
    keys = ["reward_blueprint", "support_material", "reward_random", "reward_weapon", "reward_book"]
    for key in keys:
        ItemTemplate.objects.create(key=key, name=key, effect_type="equip_weapon" if key == "reward_weapon" else "tool")
    box = _item(
        manor,
        "bound_box",
        bound_quantity=int(bound_source),
        effect_type="loot_box",
        usable=True,
        payload={
            "item_rewards": [{"item_key": "reward_blueprint", "min_quantity": 1, "max_quantity": 1}],
            "blueprint_material_support": {
                "reward_blueprint": [{"item_key": "support_material", "min_quantity": 3, "max_quantity": 3}]
            },
            "random_item_groups": [
                {
                    "chance": 1,
                    "choices": [{"item_key": "reward_random", "weight": 1}],
                    "min_quantity": 1,
                    "max_quantity": 1,
                }
            ],
            "gear_chance": 1,
            "gear_keys": ["reward_weapon"],
            "skill_book_chance": 1,
            "skill_book_keys": ["reward_book"],
        },
    )
    monkeypatch.setattr("gameplay.services.inventory.use.inventory_random.random", lambda: 0.0)
    use_inventory_item(box, manor)
    outputs = list(InventoryItem.objects.filter(manor=manor, template__key__in=keys))
    assert len(outputs) == len(keys)
    assert all(item.bound_quantity == (item.quantity if bound_source else 0) for item in outputs)
    assert not InventoryItem.objects.filter(pk=box.pk).exists()


def test_async_forging_without_blueprint_ignores_material_binding(django_user_model, monkeypatch):
    manor = _manor(django_user_model)
    material = _item(manor, "forge_bound_material", quantity=5, bound_quantity=1)
    template = ItemTemplate.objects.create(key="forge_bound_output", name="绑定锻造装备", effect_type="equip_weapon")
    monkeypatch.setattr(
        forge_service,
        "EQUIPMENT_CONFIG",
        {
            template.key: {
                "category": "dao",
                "materials": {material.template.key: 2},
                "base_duration": 10,
                "required_forging": 1,
            }
        },
    )
    monkeypatch.setattr("gameplay.services.technology.get_player_technology_level", lambda *_args, **_kwargs: 5)
    monkeypatch.setattr("gameplay.services.buildings.forge_runtime.schedule_forging_completion", lambda *_args: None)
    production = forge_service.start_equipment_forging(manor, template.key, quantity=2)
    assert production.output_is_bound is False
    assert production.output_bound_quantity == 0
    production.complete_at = timezone.now()
    production.save(update_fields=["complete_at"])
    from gameplay.services.buildings.forge_runtime import finalize_equipment_forging

    assert finalize_equipment_forging(production, send_notification=False) is True
    assert finalize_equipment_forging(production, send_notification=False) is False
    output = InventoryItem.objects.get(manor=manor, template=template)
    assert (output.quantity, output.bound_quantity) == (2, 0)
    material.refresh_from_db()
    assert (material.quantity, material.bound_quantity) == (1, 0)


def test_equipment_blueprint_splits_outputs_and_decomposition_keeps_binding(django_user_model, monkeypatch):
    manor = _manor(django_user_model)
    blueprint = _item(manor, "equip_bound_blueprint", quantity=2, bound_quantity=1)
    material = _item(manor, "equip_bound_material", quantity=4, bound_quantity=1)
    output_template = ItemTemplate.objects.create(
        key="equip_bound_result",
        name="绑定图纸装备",
        rarity="green",
        effect_type="equip_weapon",
    )
    recovered_template = ItemTemplate.objects.create(key="bound_recovered", name="回收材料", effect_type="tool")
    monkeypatch.setattr("gameplay.services.technology.get_player_technology_level", lambda *_args, **_kwargs: 5)
    monkeypatch.setattr(
        forge_service,
        "_build_blueprint_recipe_index",
        lambda: {
            blueprint.template.key: {
                "blueprint_key": blueprint.template.key,
                "result_item_key": output_template.key,
                "required_forging": 1,
                "quantity_out": 1,
                "costs": {material.template.key: 2},
            }
        },
    )
    forge_service.synthesize_equipment_with_blueprint(manor, blueprint.template.key, quantity=2)
    output = InventoryItem.objects.get(manor=manor, template=output_template)
    assert (output.quantity, output.bound_quantity) == (2, 1)
    monkeypatch.setattr(forge_service, "get_recruitment_equipment_keys", lambda: set())
    monkeypatch.setattr(forge_service, "_roll_decompose_rewards", lambda *_args: {recovered_template.key: 3})
    forge_service.decompose_equipment(manor, output_template.key, quantity=1)
    recovered = InventoryItem.objects.get(manor=manor, template=recovered_template)
    assert (recovered.quantity, recovered.bound_quantity) == (3, 3)
    output.refresh_from_db()
    assert (output.quantity, output.bound_quantity) == (1, 0)


def test_smelting_keeps_bound_material_state_until_completion(django_user_model, monkeypatch):
    from gameplay.services.buildings import smithy

    manor = _manor(django_user_model)
    material = _item(manor, "smelting_bound_input", quantity=5, bound_quantity=1)
    output_template = ItemTemplate.objects.create(key="smelting_bound_output", name="冶炼产物", effect_type="tool")
    monkeypatch.setattr(
        smithy,
        "METAL_CONFIG",
        {
            output_template.key: {
                "cost_type": material.template.key,
                "cost_amount": 2,
                "base_duration": 10,
                "required_smelting": 1,
                "category": "metal",
            }
        },
    )
    monkeypatch.setattr("gameplay.services.technology.get_player_technology_level", lambda *_args, **_kwargs: 5)
    monkeypatch.setattr(smithy, "get_smelting_queue_capacity", lambda *_args: 1)
    monkeypatch.setattr(smithy, "_schedule_smelting_completion", lambda *_args: None)
    production = smithy.start_smelting_production(manor, output_template.key, quantity=2)
    assert production.output_is_bound is True
    production.complete_at = timezone.now()
    production.save(update_fields=["complete_at"])
    assert smithy.finalize_smelting_production(production) is True
    output = InventoryItem.objects.get(manor=manor, template=output_template)
    assert (output.quantity, output.bound_quantity) == (2, 2)


@pytest.mark.parametrize("unbound", [0, 2])
def test_troop_recruitment_uses_only_non_bound_equipment(django_user_model, monkeypatch, unbound):
    from core.exceptions import TroopRecruitmentError
    from gameplay.services.recruitment import recruitment as recruitment_service

    manor = _manor(django_user_model)
    manor.retainer_count = 10
    manor.save(update_fields=["retainer_count"])
    equipment = _item(
        manor, "bound_troop_equipment", quantity=3 + unbound, bound_quantity=3, effect_type="equip_weapon"
    )
    troop = {
        "key": "bound_test_troop",
        "name": "绑定测试护院",
        "recruit": {
            "equipment": [equipment.template.key],
            "retainer_cost": 1,
            "base_duration": 10,
        },
    }
    monkeypatch.setattr(recruitment_service, "get_troop_template", lambda *_args: troop)
    monkeypatch.setattr(recruitment_service, "load_troop_templates", lambda: {"troops": [troop]})
    monkeypatch.setattr(recruitment_service, "_validate_start_recruitment_inputs", lambda *_args: troop)
    monkeypatch.setattr(recruitment_service, "_schedule_recruitment_completion", lambda *_args: None)
    requirements = recruitment_service.check_recruitment_requirements(manor, troop["key"], quantity=2)
    assert requirements["equipment_status"][equipment.template.key]["have"] == unbound
    options = recruitment_service.get_recruitment_options(manor)
    assert options[0]["equipment"][0]["have"] == unbound
    if unbound:
        quote = recruitment_service.quote_troop_recruitment(manor, troop["key"], quantity=2)
        assert quote.equipment_costs == ((equipment.template.key, 2),)
        recruitment_service.start_troop_recruitment(manor, troop["key"], quantity=2)
        equipment.refresh_from_db()
        assert (equipment.quantity, equipment.bound_quantity) == (3, 3)
    else:
        with pytest.raises(TroopRecruitmentError, match="装备不足"):
            recruitment_service.start_troop_recruitment(manor, troop["key"], quantity=2)
        equipment.refresh_from_db()
        assert (equipment.quantity, equipment.bound_quantity) == (3, 3)
        manor.refresh_from_db()
        assert manor.retainer_count == 10


def test_reset_fallback_returns_bound_gear_once_without_free_duplicate(django_user_model, monkeypatch):
    from core.exceptions import GameError
    from gameplay.services.inventory.guest_reset_helpers import detach_guest_gears_for_reset

    manor = _manor(django_user_model)
    guest_template = GuestTemplate.objects.create(key="bound_reset_guest", name="绑定卸装门客", base_hp=1000)
    guest = Guest.objects.create(manor=manor, template=guest_template, force=100, defense_stat=100)
    weapon = _item(manor, "bound_reset_weapon", bound_quantity=1, effect_type="equip_weapon", payload={"force": 5})
    gear = equip_guest(weapon.template.key, guest)
    monkeypatch.setattr(
        "guests.services.equipment.unequip_guest_item",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(GameError(message="卸装失败")),
    )
    with transaction.atomic():
        assert detach_guest_gears_for_reset(guest, action_label="测试") == 1
    gear.refresh_from_db()
    returned = InventoryItem.objects.get(manor=manor, template=weapon.template)
    assert (returned.quantity, returned.bound_quantity) == (1, 1)
    assert gear.inventory_backed is True
    equipped_again = equip_guest(gear, guest)
    assert equipped_again.is_bound is True
    assert not InventoryItem.objects.filter(pk=returned.pk).exists()
