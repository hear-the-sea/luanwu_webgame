from __future__ import annotations

import pytest
from django.db import transaction

from core.exceptions import ForgeOperationError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.buildings import forge_blueprints
from gameplay.services.equipment_affixes import equipment_affix_key, get_or_create_equipment_affix_template
from gameplay.services.manor.core import ensure_manor

pytestmark = pytest.mark.django_db

ARMOR_KEYS = (
    "equip_tianshukaijia",
    "equip_tianxuankaijia",
    "equip_tianjikaijia",
    "equip_yaoguangkaijia",
    "equip_tianquankaijia",
    "equip_yuhengkaijia",
    "equip_kaiyangkaijia",
)


@pytest.fixture
def forging_world(django_user_model, monkeypatch):
    manor = ensure_manor(django_user_model.objects.create_user(username="chiyou_forging"))
    blueprint, _ = ItemTemplate.objects.update_or_create(
        key="blueprint_chiyoujia",
        defaults={"name": "蚩尤甲图纸", "effect_type": "tool"},
    )
    result, _ = ItemTemplate.objects.update_or_create(
        key="equip_chiyoujia",
        defaults={"name": "蚩尤甲", "effect_type": "equip_armor", "rarity": "orange"},
    )
    bases = []
    for key in ARMOR_KEYS:
        base, _ = ItemTemplate.objects.update_or_create(
            key=key,
            defaults={
                "name": key,
                "effect_type": "equip_armor",
                "rarity": "purple",
                "effect_payload": {"force": 30, "defense": 60},
            },
        )
        bases.append(base)
    recipe = {
        "blueprint_key": blueprint.key,
        "result_item_key": result.key,
        "required_forging": 10,
        "quantity_out": 1,
        "costs": {key: 1 for key in ARMOR_KEYS},
    }
    monkeypatch.setattr("gameplay.services.technology.get_player_technology_level", lambda *_args: 10)
    monkeypatch.setattr(
        forge_blueprints,
        "roll_equipment_reward_items_locked",
        lambda items: (items, {result.key: result}),
    )
    InventoryItem.objects.create(manor=manor, template=blueprint, quantity=10)
    return manor, blueprint, result, bases, recipe


def _stock(manor, template, quantity=1, bound=0, location="warehouse"):
    return InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=quantity,
        bound_quantity=bound,
        storage_location=location,
    )


def _variant(base, affix="force"):
    with transaction.atomic():
        return get_or_create_equipment_affix_template(base, affix)


def _craft(world, quantity=1):
    manor, blueprint, _result, _bases, recipe = world
    return forge_blueprints.synthesize_equipment_with_blueprint(
        manor, blueprint.key, quantity, recipe_index={blueprint.key: recipe}
    )


def test_seven_distinct_armor_materials_accept_mixed_affix_inventory(forging_world):
    manor, blueprint, result, bases, _recipe = forging_world
    stocks = [_stock(manor, base if index % 2 else _variant(base)) for index, base in enumerate(bases)]

    crafted = _craft(forging_world)

    assert crafted["items"] == {result.key: 1}
    assert crafted["bound_quantity"] == 0
    assert not InventoryItem.objects.filter(pk__in=[item.pk for item in stocks]).exists()
    assert InventoryItem.objects.get(manor=manor, template=blueprint).quantity == 9


def test_options_include_affixes_and_limit_by_rarest_material(forging_world):
    manor, _blueprint, _result, bases, recipe = forging_world
    for index, base in enumerate(bases):
        _stock(manor, _variant(base), quantity=2 if index else 1)

    option = forge_blueprints.get_blueprint_synthesis_options(manor, config={"recipes": [recipe]})[0]

    assert option["can_synthesize"] is True
    assert option["max_synthesis_quantity"] == 1
    assert [cost["current"] for cost in option["costs"]] == [1, 2, 2, 2, 2, 2, 2]


def test_consumption_uses_base_before_variants_and_keeps_extra_stocks(forging_world):
    manor, _blueprint, _result, bases, _recipe = forging_world
    extra_variant = _stock(manor, _variant(bases[0]))
    base_stock = _stock(manor, bases[0])
    for base in bases[1:]:
        _stock(manor, base)

    _craft(forging_world)

    assert not InventoryItem.objects.filter(pk=base_stock.pk).exists()
    extra_variant.refresh_from_db()
    assert extra_variant.quantity == 1


def test_affix_variants_are_consumed_in_inventory_id_order(forging_world):
    manor, _blueprint, _result, bases, _recipe = forging_world
    first = _stock(manor, _variant(bases[0], "intellect"))
    second = _stock(manor, _variant(bases[0], "force"))
    for base in bases[1:]:
        _stock(manor, base)

    _craft(forging_world)

    assert not InventoryItem.objects.filter(pk=first.pk).exists()
    second.refresh_from_db()
    assert second.quantity == 1


@pytest.mark.parametrize("bound_source", ["base", "affix", "blueprint"])
def test_bound_equipment_materials_and_blueprints_bind_output(forging_world, bound_source):
    manor, blueprint, result, bases, _recipe = forging_world
    for index, base in enumerate(bases):
        template = _variant(base) if bound_source == "affix" else base
        _stock(
            manor,
            template,
            bound=1 if index == 0 and bound_source != "blueprint" else 0,
        )
    if bound_source == "blueprint":
        InventoryItem.objects.filter(manor=manor, template=blueprint).update(bound_quantity=1)

    crafted = _craft(forging_world)

    assert crafted["bound_quantity"] == 1
    assert InventoryItem.objects.get(manor=manor, template=result).bound_quantity == 1


def test_batch_combines_base_and_affixes_and_preserves_binding(forging_world):
    manor, _blueprint, result, bases, _recipe = forging_world
    for index, base in enumerate(bases):
        _stock(manor, base)
        _stock(manor, _variant(base), bound=1 if index == 0 else 0)

    crafted = _craft(forging_world, quantity=2)

    assert (
        crafted["quantity"],
        crafted["bound_quantity"],
        crafted["unbound_quantity"],
    ) == (2, 1, 1)
    output = InventoryItem.objects.get(manor=manor, template=result)
    assert (output.quantity, output.bound_quantity) == (2, 1)


@pytest.mark.parametrize(
    "invalid_source",
    [
        "missing",
        "treasury",
        "other_manor",
        "fake_key",
        "fake_metadata",
        "fake_affix",
        "fake_type",
        "fake_rarity",
    ],
)
def test_missing_or_invalid_material_leaves_every_input_untouched(forging_world, django_user_model, invalid_source):
    manor, blueprint, result, bases, recipe = forging_world
    for base in bases[:-1]:
        _stock(manor, _variant(base))
    base = bases[-1]
    if invalid_source == "treasury":
        _stock(manor, _variant(base), location="treasury")
    elif invalid_source == "other_manor":
        other = ensure_manor(django_user_model.objects.create_user(username="other_armor_owner"))
        _stock(other, _variant(base))
    elif invalid_source == "fake_key":
        fake = ItemTemplate.objects.create(
            key="fake_seventh_armor",
            name="伪造材料",
            effect_type="equip_armor",
            rarity="purple",
            effect_payload={"equipment_base_key": base.key, "equipment_affix": "force"},
        )
        _stock(manor, fake)
    elif invalid_source in {"fake_metadata", "fake_affix", "fake_type", "fake_rarity"}:
        fake = ItemTemplate.objects.create(
            key=equipment_affix_key(base.key, "force"),
            name="伪造词缀身份",
            effect_type="equip_weapon" if invalid_source == "fake_type" else "equip_armor",
            rarity="orange" if invalid_source == "fake_rarity" else "purple",
            effect_payload={
                "equipment_base_key": bases[0].key if invalid_source == "fake_metadata" else base.key,
                "equipment_affix": "defense" if invalid_source == "fake_affix" else "force",
            },
        )
        _stock(manor, fake)
    before = list(InventoryItem.objects.filter(manor=manor).values_list("pk", "quantity", "bound_quantity"))

    with pytest.raises(ForgeOperationError, match="不足"):
        _craft(forging_world)

    assert list(InventoryItem.objects.filter(manor=manor).values_list("pk", "quantity", "bound_quantity")) == before
    assert not InventoryItem.objects.filter(manor=manor, template=result).exists()
    option = forge_blueprints.get_blueprint_synthesis_options(manor, config={"recipes": [recipe]})[0]
    assert option["can_synthesize"] is False
    assert option["costs"][-1]["current"] == 0
    assert InventoryItem.objects.get(manor=manor, template=blueprint).quantity == 10


def test_chiyou_recipe_requires_forging_ten_without_consuming_inputs(forging_world, monkeypatch):
    manor, blueprint, result, bases, _recipe = forging_world
    for base in bases:
        _stock(manor, base)
    monkeypatch.setattr("gameplay.services.technology.get_player_technology_level", lambda *_args: 9)

    with pytest.raises(ForgeOperationError, match="需要锻造技10级"):
        _craft(forging_world)

    assert InventoryItem.objects.get(manor=manor, template=blueprint).quantity == 10
    assert InventoryItem.objects.filter(manor=manor, template__in=bases, quantity=1).count() == 7
    assert not InventoryItem.objects.filter(manor=manor, template=result).exists()


def test_equipped_armor_is_not_available_as_forging_material(forging_world):
    from guests.models import GearItem, GearTemplate, Guest, GuestTemplate

    manor, _blueprint, _result, bases, recipe = forging_world
    for base in bases[:-1]:
        _stock(manor, base)
    guest_template = GuestTemplate.objects.create(key="armor_owner", name="甲胄主人", archetype="military")
    guest = Guest.objects.create(manor=manor, template=guest_template, level=10)
    gear_template = GearTemplate.objects.create(key=bases[-1].key, name="已装备紫甲", slot="armor", rarity="purple")
    gear = GearItem.objects.create(manor=manor, template=gear_template, guest=guest)

    with pytest.raises(ForgeOperationError, match="不足"):
        _craft(forging_world)

    gear.refresh_from_db()
    assert gear.guest_id == guest.pk
    option = forge_blueprints.get_blueprint_synthesis_options(manor, config={"recipes": [recipe]})[0]
    assert option["costs"][-1]["current"] == 0
