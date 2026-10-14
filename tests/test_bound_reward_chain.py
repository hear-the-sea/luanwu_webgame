import pytest

from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services import luanwu_shop
from gameplay.services.buildings.forge_blueprints import synthesize_equipment_with_blueprint
from gameplay.services.buildings.forge_helpers import build_blueprint_synthesis_option
from gameplay.services.inventory.use import use_inventory_item
from gameplay.services.manor.core import ensure_manor


@pytest.mark.parametrize("stock,expected_max,can_afford", [(6, 3, True), (1, 0, False)])
def test_recipe_limit_includes_primary_blueprint_cost(stock, expected_max, can_afford):
    option = build_blueprint_synthesis_option(
        {
            "blueprint_key": "blueprint_repeated",
            "result_item_key": "equip_result",
            "required_forging": 1,
            "costs": {"blueprint_repeated": 1},
        },
        quantities={"blueprint_repeated": stock},
        template_map={},
        forging_level=5,
    )
    assert option["max_synthesis_quantity"] == expected_max
    assert option["can_afford"] is can_afford
    assert option["costs"][0]["required"] == 2


@pytest.mark.django_db
def test_bound_coin_chest_blueprint_equipment_chain_preserves_mixed_quantities(django_user_model, monkeypatch):
    manor = ensure_manor(django_user_model.objects.create_user(username="bound_reward_chain"))
    coin, _ = ItemTemplate.objects.get_or_create(key="chunqiu_coin", defaults={"name": "春秋币"})
    InventoryItem.objects.create(manor=manor, template=coin, quantity=6, bound_quantity=3)
    blueprint = ItemTemplate.objects.create(key="blueprint_binding_chain", name="装备图纸", effect_type="tool")
    material = ItemTemplate.objects.create(key="binding_chain_material", name="合成材料", effect_type="tool")
    medicine = ItemTemplate.objects.create(key="binding_chain_medicine", name="药品", effect_type="medicine")
    chest_gear = ItemTemplate.objects.create(
        key="equip_binding_chain_drop", name="宝箱装备", effect_type="equip_weapon"
    )
    result = ItemTemplate.objects.create(key="equip_binding_chain_result", name="图纸装备", effect_type="equip_weapon")
    box = ItemTemplate.objects.create(
        key="binding_chain_chest",
        name="图纸宝箱",
        effect_type="loot_box",
        is_usable=True,
        effect_payload={
            "item_rewards": [{"item_key": blueprint.key, "min_quantity": 1, "max_quantity": 1}],
            "blueprint_material_support": {
                blueprint.key: [{"item_key": material.key, "min_quantity": 2, "max_quantity": 2}]
            },
            "random_item_groups": [
                {"chance": 1, "choices": [{"item_key": medicine.key, "weight": 1}], "min_quantity": 3}
            ],
            "gear_chance": 1,
            "gear_keys": [chest_gear.key],
        },
    )
    product = luanwu_shop.LuanwuShopProductConfig(
        key="chain_chest", name="图纸宝箱", description=None, price=3, item_key=box.key
    )
    config = luanwu_shop.LuanwuShopConfig(currency_item_key=coin.key, products=(product,))
    monkeypatch.setattr(luanwu_shop, "load_luanwu_shop_config", lambda: config)
    monkeypatch.setattr("gameplay.services.technology.get_player_technology_level", lambda *_args, **_kwargs: 5)
    luanwu_shop.purchase_luanwu_shop_item(manor, product.key, quantity=2)
    stock = InventoryItem.objects.get(manor=manor, template=box)
    assert (stock.quantity, stock.bound_quantity) == (2, 1)

    use_inventory_item(stock, manor)
    stock.refresh_from_db()
    assert (stock.quantity, stock.bound_quantity) == (1, 0)
    assert InventoryItem.objects.get(manor=manor, template=blueprint).bound_quantity == 1
    assert InventoryItem.objects.get(manor=manor, template=material).bound_quantity == 2
    assert InventoryItem.objects.get(manor=manor, template=medicine).bound_quantity == 3
    assert InventoryItem.objects.get(manor=manor, template=chest_gear).bound_quantity == 1
    use_inventory_item(stock, manor)

    synthesize_equipment_with_blueprint(
        manor,
        blueprint.key,
        quantity=2,
        recipe_index={
            blueprint.key: {
                "blueprint_key": blueprint.key,
                "result_item_key": result.key,
                "required_forging": 1,
                "quantity_out": 1,
                "costs": {material.key: 2},
            }
        },
    )
    equipment = InventoryItem.objects.get(manor=manor, template=result)
    assert (equipment.quantity, equipment.bound_quantity, equipment.unbound_quantity) == (2, 1, 1)
    assert not InventoryItem.objects.filter(manor=manor, template__in=[coin, blueprint, material, box]).exists()
    medicine_stock = InventoryItem.objects.get(manor=manor, template=medicine)
    gear_stock = InventoryItem.objects.get(manor=manor, template=chest_gear)
    assert (medicine_stock.quantity, medicine_stock.bound_quantity) == (6, 3)
    assert (gear_stock.quantity, gear_stock.bound_quantity) == (2, 1)
