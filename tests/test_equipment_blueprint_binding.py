from __future__ import annotations

import pytest
from django.utils import timezone

from gameplay.models import EquipmentProduction, InventoryItem, ItemTemplate
from gameplay.services.buildings import forge
from gameplay.services.buildings.forge_runtime import finalize_equipment_forging
from gameplay.services.inventory.use import use_inventory_item
from gameplay.services.manor.core import ensure_manor

pytestmark = pytest.mark.django_db


def _manor(django_user_model):
    return ensure_manor(django_user_model.objects.create_user(username="blueprint_binding", password="test"))


def _stock(manor, key, total, bound=0, **kwargs):
    template = ItemTemplate.objects.create(key=key, name=key, effect_type="tool", **kwargs)
    return InventoryItem.objects.create(manor=manor, template=template, quantity=total, bound_quantity=bound)


@pytest.mark.parametrize("bound_blueprints", [0, 1, 2])
def test_blueprint_batch_only_binds_outputs_of_consumed_bound_blueprints(
    django_user_model,
    monkeypatch,
    bound_blueprints,
):
    manor = _manor(django_user_model)
    blueprint = _stock(manor, "blueprint_batch", 2, bound_blueprints)
    material = _stock(manor, "batch_material", 4, 4)
    result = ItemTemplate.objects.create(
        key="batch_equipment",
        name="批量装备",
        effect_type="equip_weapon",
        default_bound=True,
    )
    monkeypatch.setattr("gameplay.services.technology.get_player_technology_level", lambda *_args: 5)
    monkeypatch.setattr(
        forge,
        "_build_blueprint_recipe_index",
        lambda: {
            blueprint.template.key: {
                "blueprint_key": blueprint.template.key,
                "result_item_key": result.key,
                "required_forging": 1,
                "quantity_out": 3,
                "costs": {material.template.key: 2},
            }
        },
    )
    crafted = forge.synthesize_equipment_with_blueprint(manor, blueprint.template.key, quantity=2)
    assert (crafted["bound_quantity"], crafted["unbound_quantity"]) == (
        bound_blueprints * 3,
        6 - bound_blueprints * 3,
    )
    output = InventoryItem.objects.get(manor=manor, template=result)
    assert (output.quantity, output.bound_quantity) == (6, bound_blueprints * 3)


@pytest.mark.parametrize("bound_blueprints,expected_bound", [(0, 0), (1, 1), (3, 2), (4, 2)])
def test_queue_forging_splits_by_bound_blueprints_in_materials(
    django_user_model,
    monkeypatch,
    bound_blueprints,
    expected_bound,
):
    manor = _manor(django_user_model)
    blueprint = _stock(manor, "blueprint_queued", 4, bound_blueprints)
    material = _stock(manor, "queued_material", 4, 4)
    result = ItemTemplate.objects.create(key="queued_equipment", name="队列装备", effect_type="equip_weapon")
    monkeypatch.setattr(
        forge,
        "EQUIPMENT_CONFIG",
        {
            result.key: {
                "category": "dao",
                "materials": {blueprint.template.key: 2, material.template.key: 2},
                "base_duration": 10,
                "required_forging": 1,
            }
        },
    )
    monkeypatch.setattr("gameplay.services.technology.get_player_technology_level", lambda *_args: 5)
    monkeypatch.setattr("gameplay.services.buildings.forge_runtime.schedule_forging_completion", lambda *_args: None)
    production = forge.start_equipment_forging(manor, result.key, quantity=2)
    assert production.output_bound_quantity == expected_bound
    production.complete_at = timezone.now()
    production.save(update_fields=["complete_at"])
    assert finalize_equipment_forging(production, send_notification=False) is True
    output = InventoryItem.objects.get(manor=manor, template=result)
    assert (output.quantity, output.bound_quantity) == (2, expected_bound)


@pytest.mark.parametrize(
    "source_key,bound_source,required_blueprint,expected_bound",
    [
        ("ordinary_synthesis_tool", 1, False, 0),
        ("blueprint_direct", 0, False, 0),
        ("blueprint_direct", 1, False, 2),
        ("ordinary_synthesis_tool", 0, True, 2),
    ],
)
def test_direct_equipment_synthesis_requires_a_bound_blueprint(
    django_user_model,
    source_key,
    bound_source,
    required_blueprint,
    expected_bound,
):
    manor = _manor(django_user_model)
    material = _stock(manor, "direct_material", 3, 3)
    required_items = {material.template.key: 3}
    if required_blueprint:
        _stock(manor, "blueprint_required", 1, 1)
        required_items["blueprint_required"] = 1
    result = ItemTemplate.objects.create(
        key="direct_equipment",
        name="即时装备",
        effect_type="equip_weapon",
        default_bound=True,
    )
    source = _stock(
        manor,
        source_key,
        1,
        bound_source,
        is_usable=True,
        effect_payload={
            "action": "synthesize_item",
            "result_item_key": result.key,
            "result_quantity": 2,
            "required_items": required_items,
        },
    )
    use_inventory_item(source, manor)
    output = InventoryItem.objects.get(manor=manor, template=result)
    assert (output.quantity, output.bound_quantity) == (2, expected_bound)


def test_legacy_bound_forging_queue_keeps_promised_binding(django_user_model):
    manor = _manor(django_user_model)
    result = ItemTemplate.objects.create(key="legacy_equipment", name="历史装备", effect_type="equip_weapon")
    production = EquipmentProduction.objects.create(
        manor=manor,
        equipment_key=result.key,
        equipment_name=result.name,
        quantity=2,
        output_is_bound=True,
        base_duration=10,
        actual_duration=10,
        complete_at=timezone.now(),
    )
    assert finalize_equipment_forging(production, send_notification=False) is True
    output = InventoryItem.objects.get(manor=manor, template=result)
    assert (output.quantity, output.bound_quantity) == (2, 2)


def test_extra_blueprint_in_recipe_costs_controls_binding(django_user_model, monkeypatch):
    manor = _manor(django_user_model)
    blueprint = _stock(manor, "blueprint_primary", 2)
    extra = _stock(manor, "blueprint_secondary", 2, 1)
    result = ItemTemplate.objects.create(
        key="extra_blueprint_equipment", name="额外图纸装备", effect_type="equip_weapon"
    )
    config = {
        "recipes": [
            {
                "blueprint_key": blueprint.template.key,
                "result_item_key": result.key,
                "required_forging": 1,
                "quantity_out": 1,
                "costs": {extra.template.key: 1},
            }
        ]
    }
    monkeypatch.setattr("gameplay.services.technology.get_player_technology_level", lambda *_args: 5)
    monkeypatch.setattr(forge, "_build_blueprint_recipe_index", lambda: {blueprint.template.key: config["recipes"][0]})
    monkeypatch.setattr(forge, "load_forge_blueprint_config", lambda: config)
    option = forge.get_blueprint_synthesis_options(manor)[0]
    assert (option["blueprint_bound_count"], option["blueprint_unbound_count"]) == (0, 2)
    crafted = forge.synthesize_equipment_with_blueprint(manor, blueprint.template.key, quantity=2)
    assert (crafted["bound_quantity"], crafted["unbound_quantity"]) == (1, 1)
    output = InventoryItem.objects.get(manor=manor, template=result)
    assert (output.quantity, output.bound_quantity) == (2, 1)


def test_primary_blueprint_also_in_costs_is_consumed_and_allocated_once(django_user_model, monkeypatch):
    manor = _manor(django_user_model)
    blueprint = _stock(manor, "blueprint_repeated", 6, 3)
    result = ItemTemplate.objects.create(
        key="repeated_blueprint_equipment", name="重复图纸装备", effect_type="equip_weapon"
    )
    monkeypatch.setattr("gameplay.services.technology.get_player_technology_level", lambda *_args: 5)
    monkeypatch.setattr(
        forge,
        "_build_blueprint_recipe_index",
        lambda: {
            blueprint.template.key: {
                "blueprint_key": blueprint.template.key,
                "result_item_key": result.key,
                "required_forging": 1,
                "quantity_out": 1,
                "costs": {blueprint.template.key: 1},
            }
        },
    )
    crafted = forge.synthesize_equipment_with_blueprint(manor, blueprint.template.key, quantity=3)
    assert (crafted["bound_quantity"], crafted["unbound_quantity"]) == (2, 1)
    output = InventoryItem.objects.get(manor=manor, template=result)
    assert (output.quantity, output.bound_quantity) == (3, 2)
    assert not InventoryItem.objects.filter(pk=blueprint.pk).exists()
