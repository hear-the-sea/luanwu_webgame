from __future__ import annotations

from core.utils.yaml_schema import (
    validate_auction_items,
    validate_forge_blueprints,
    validate_forge_decompose,
    validate_warehouse_production,
)
from tests.yaml_schema_new_configs.support import assert_invalid


def test_warehouse_production_rejects_non_dict_root():
    result = validate_warehouse_production("not a dict")
    assert_invalid(result, substring="expected a mapping")


def test_warehouse_production_rejects_missing_levels():
    result = validate_warehouse_production({"equipment": {}})
    assert_invalid(result, substring="missing required key 'levels'")


def test_warehouse_production_rejects_negative_contribution_cost():
    data = {"equipment": {"levels": {1: [{"item_key": "gear_green_random", "quantity": 2, "contribution_cost": -10}]}}}
    result = validate_warehouse_production(data)
    assert_invalid(result, substring="contribution_cost")


def test_warehouse_production_rejects_unknown_tech_key():
    data = {"unknown_tech": {"levels": {1: [{"item_key": "x", "quantity": 1, "contribution_cost": 10}]}}}
    result = validate_warehouse_production(data)
    assert_invalid(result, substring="unknown tech section")


def test_warehouse_production_accepts_guard_tech_key():
    data = {"guard": {"levels": {1: [{"item_key": "guild_guard_box_basic", "quantity": 1, "contribution_cost": 80}]}}}
    result = validate_warehouse_production(data)
    assert result.is_valid, result.errors


def test_warehouse_production_rejects_negative_weekly_personal_limit():
    data = {
        "equipment": {
            "levels": {
                1: [
                    {
                        "item_key": "guild_gear_box_green",
                        "quantity": 1,
                        "contribution_cost": 70,
                        "weekly_personal_limit": -1,
                    }
                ]
            }
        }
    }
    result = validate_warehouse_production(data)
    assert_invalid(result, substring="weekly_personal_limit")


def test_auction_items_rejects_non_dict_root():
    result = validate_auction_items([])
    assert_invalid(result, substring="expected a mapping")


def test_auction_items_rejects_missing_items():
    result = validate_auction_items({"settings": {"cycle_days": 3}})
    assert_invalid(result, substring="missing required key 'items'")


def test_auction_items_rejects_zero_starting_price():
    data = {"items": [{"item_key": "some_item", "slots": 1, "quantity_per_slot": 1, "starting_price": 0}]}
    result = validate_auction_items(data)
    assert_invalid(result, substring="starting_price")


def test_auction_items_rejects_duplicate_item_keys():
    data = {
        "items": [
            {"item_key": "item_a", "slots": 1, "quantity_per_slot": 1, "starting_price": 5},
            {"item_key": "item_a", "slots": 1, "quantity_per_slot": 1, "starting_price": 5},
        ]
    }
    result = validate_auction_items(data)
    assert_invalid(result, substring="duplicate")


def test_forge_blueprints_rejects_non_dict_root():
    result = validate_forge_blueprints("bad")
    assert_invalid(result)


def test_forge_blueprints_rejects_missing_recipes():
    result = validate_forge_blueprints({})
    assert_invalid(result, substring="missing required key 'recipes'")


def test_forge_blueprints_rejects_zero_quantity_out():
    data = {
        "recipes": [
            {
                "blueprint_key": "bp_x",
                "result_item_key": "equip_x",
                "required_forging": 5,
                "quantity_out": 0,
            }
        ]
    }
    result = validate_forge_blueprints(data)
    assert_invalid(result, substring="quantity_out")


def test_forge_blueprints_rejects_non_dict_costs():
    data = {
        "recipes": [
            {
                "blueprint_key": "bp_x",
                "result_item_key": "equip_x",
                "required_forging": 5,
                "quantity_out": 1,
                "costs": "not a dict",
            }
        ]
    }
    result = validate_forge_blueprints(data)
    assert_invalid(result, substring="'costs' expected a mapping")


def test_forge_decompose_rejects_non_dict_root():
    result = validate_forge_decompose(42)
    assert_invalid(result)


def test_forge_decompose_rejects_missing_supported_rarities():
    result = validate_forge_decompose({"base_materials": {}})
    assert_invalid(result, substring="missing required key 'supported_rarities'")


def test_forge_decompose_rejects_unknown_rarity_in_supported():
    result = validate_forge_decompose({"supported_rarities": ["green", "legendary"]})
    assert_invalid(result, substring="unknown rarity 'legendary'")


def test_forge_decompose_rejects_probability_out_of_range():
    data = {
        "supported_rarities": ["green"],
        "chance_rewards": {"green": {"wood_essence": 1.5}},
    }
    result = validate_forge_decompose(data)
    assert_invalid(result, substring="probability must be between 0 and 1")


def test_forge_decompose_rejects_invalid_material_range():
    data = {
        "supported_rarities": ["green"],
        "base_materials": {"green": {"tong": [2]}},
    }
    result = validate_forge_decompose(data)
    assert_invalid(result, substring="list of [min, max]")
