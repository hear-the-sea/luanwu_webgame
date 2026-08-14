from __future__ import annotations

from core.utils.yaml_schema import (
    validate_building_templates,
    validate_guest_templates,
    validate_item_templates,
    validate_troop_templates,
)
from tests.yaml_schema.support import assert_has_error, assert_valid


class TestItemTemplatesValidation:
    def test_valid_minimal(self):
        data = {"items": [{"key": "grain", "name": "Grain", "effect_type": "resource"}]}
        assert_valid(validate_item_templates(data))

    def test_valid_full_entry(self):
        data = {
            "items": [
                {
                    "key": "grain",
                    "name": "Grain",
                    "effect_type": "resource",
                    "rarity": "black",
                    "tradeable": True,
                    "price": 10,
                    "storage_space": 1,
                    "is_usable": False,
                }
            ]
        }
        assert_valid(validate_item_templates(data))

    def test_missing_root_key(self):
        result = validate_item_templates({})
        assert_has_error(result, substring="missing required key 'items'")

    def test_non_dict_root(self):
        result = validate_item_templates([1, 2, 3])
        assert_has_error(result, substring="expected a mapping at root level")

    def test_missing_required_fields(self):
        data = {"items": [{"key": "grain"}]}
        result = validate_item_templates(data)
        assert_has_error(result, substring="missing required field 'name'")
        assert_has_error(result, substring="missing required field 'effect_type'")

    def test_invalid_effect_type(self):
        data = {"items": [{"key": "x", "name": "X", "effect_type": "invalid"}]}
        result = validate_item_templates(data)
        assert_has_error(result, substring="not in allowed set")

    def test_invalid_rarity(self):
        data = {"items": [{"key": "x", "name": "X", "effect_type": "tool", "rarity": "legendary"}]}
        result = validate_item_templates(data)
        assert_has_error(result, substring="not in allowed set")

    def test_negative_price(self):
        data = {"items": [{"key": "x", "name": "X", "effect_type": "tool", "price": -10}]}
        result = validate_item_templates(data)
        assert_has_error(result, substring="must be >= 0")

    def test_storage_space_zero(self):
        data = {"items": [{"key": "x", "name": "X", "effect_type": "tool", "storage_space": 0}]}
        result = validate_item_templates(data)
        assert_has_error(result, substring="must be >= 1")

    def test_wrong_type_tradeable(self):
        data = {"items": [{"key": "x", "name": "X", "effect_type": "tool", "tradeable": "yes"}]}
        result = validate_item_templates(data)
        assert_has_error(result, substring="expected bool")

    def test_duplicate_keys(self):
        data = {
            "items": [
                {"key": "dup", "name": "A", "effect_type": "tool"},
                {"key": "dup", "name": "B", "effect_type": "tool"},
            ]
        }
        result = validate_item_templates(data)
        assert_has_error(result, substring="duplicate key 'dup'")

    def test_non_mapping_item(self):
        data = {"items": [{"key": "ok", "name": "Ok", "effect_type": "tool"}, "bad_entry"]}
        result = validate_item_templates(data)
        assert_has_error(result, substring="expected a mapping")

    def test_all_equip_effect_types_accepted(self):
        equip_types = [
            "equip_helmet",
            "equip_armor",
            "equip_shoes",
            "equip_weapon",
            "equip_mount",
            "equip_ornament",
            "equip_device",
        ]
        items = [
            {"key": f"eq_{index}", "name": f"Eq{index}", "effect_type": effect_type}
            for index, effect_type in enumerate(equip_types)
        ]
        assert_valid(validate_item_templates({"items": items}))


class TestBuildingTemplatesValidation:
    def test_valid_minimal(self):
        data = {"buildings": [{"key": "farm", "name": "Farm", "category": "resource", "resource_type": "grain"}]}
        assert_valid(validate_building_templates(data))

    def test_missing_buildings_key(self):
        result = validate_building_templates({})
        assert_has_error(result, substring="missing required key 'buildings'")

    def test_invalid_category(self):
        data = {"buildings": [{"key": "b", "name": "B", "category": "invalid", "resource_type": "grain"}]}
        result = validate_building_templates(data)
        assert_has_error(result, substring="not in allowed set")

    def test_invalid_resource_type(self):
        data = {"buildings": [{"key": "b", "name": "B", "category": "resource", "resource_type": "gold"}]}
        result = validate_building_templates(data)
        assert_has_error(result, substring="not in allowed set")

    def test_negative_base_rate(self):
        data = {
            "buildings": [
                {
                    "key": "b",
                    "name": "B",
                    "category": "resource",
                    "resource_type": "grain",
                    "base_rate_per_hour": -5,
                }
            ]
        }
        result = validate_building_templates(data)
        assert_has_error(result, substring="must be >= 0")

    def test_base_cost_non_dict(self):
        data = {
            "buildings": [
                {
                    "key": "b",
                    "name": "B",
                    "category": "resource",
                    "resource_type": "grain",
                    "base_cost": "invalid",
                }
            ]
        }
        result = validate_building_templates(data)
        assert_has_error(result, substring="expected a mapping")

    def test_upgrade_budget_fields_validate_shape_and_curve(self):
        data = {
            "buildings": [
                {
                    "key": "b",
                    "name": "B",
                    "category": "resource",
                    "resource_type": "grain",
                    "upgrade_time_budget": 0,
                    "time_curve": 0.9,
                    "upgrade_cost_budget": {"silver": "invalid"},
                }
            ]
        }
        result = validate_building_templates(data)
        assert_has_error(result, substring="upgrade_time_budget")
        assert_has_error(result, substring="time_curve")
        assert_has_error(result, substring="upgrade_cost_budget.silver")

    def test_upgrade_cost_budget_must_be_mapping(self):
        data = {
            "buildings": [
                {
                    "key": "b",
                    "name": "B",
                    "category": "resource",
                    "resource_type": "grain",
                    "upgrade_cost_budget": 100,
                }
            ]
        }
        result = validate_building_templates(data)
        assert_has_error(result, substring="field 'upgrade_cost_budget' expected a mapping")

    def test_duplicate_keys(self):
        building = {"key": "dup", "name": "D", "category": "resource", "resource_type": "grain"}
        data = {"buildings": [building, dict(building)]}
        result = validate_building_templates(data)
        assert_has_error(result, substring="duplicate key 'dup'")

    def test_categories_validation(self):
        data = {
            "categories": [
                {"key": "resource", "name": "Resource"},
                {"name": "NoKey"},
            ],
            "buildings": [],
        }
        result = validate_building_templates(data)
        assert_has_error(result, substring="missing required field 'key'")


class TestGuestTemplatesValidation:
    def test_valid_pools_and_profiles(self):
        data = {
            "pools": [
                {"key": "cunmu", "name": "Village", "cost": {"silver": 500}, "cooldown_seconds": 600, "draw_count": 1}
            ],
            "attribute_profiles": {
                "black": {
                    "military": {"force": 29, "intellect": 8},
                    "civil": {"force": 20, "intellect": 22},
                }
            },
        }
        assert_valid(validate_guest_templates(data))

    def test_pool_missing_key(self):
        data = {"pools": [{"name": "Test"}]}
        result = validate_guest_templates(data)
        assert_has_error(result, substring="missing required field 'key'")

    def test_negative_draw_count(self):
        data = {"pools": [{"key": "p", "name": "P", "draw_count": 0}]}
        result = validate_guest_templates(data)
        assert_has_error(result, substring="must be >= 1")

    def test_unknown_rarity_in_profiles(self):
        data = {"attribute_profiles": {"legendary": {"military": {"force": 99}}}}
        result = validate_guest_templates(data)
        assert_has_error(result, substring="unknown rarity")

    def test_unknown_archetype_in_profiles(self):
        data = {"attribute_profiles": {"black": {"wizard": {"force": 99}}}}
        result = validate_guest_templates(data)
        assert_has_error(result, substring="unknown archetype")


class TestTroopTemplatesValidation:
    def test_valid_minimal(self):
        data = {"troops": [{"key": "scout", "name": "Scout"}]}
        assert_valid(validate_troop_templates(data))

    def test_missing_troops_key(self):
        result = validate_troop_templates({})
        assert_has_error(result, substring="missing required key 'troops'")

    def test_missing_name(self):
        data = {"troops": [{"key": "scout"}]}
        result = validate_troop_templates(data)
        assert_has_error(result, substring="missing required field 'name'")

    def test_recruit_not_mapping(self):
        data = {"troops": [{"key": "scout", "name": "Scout", "recruit": "bad"}]}
        result = validate_troop_templates(data)
        assert_has_error(result, substring="expected a mapping")

    def test_recruit_equipment_not_list(self):
        data = {"troops": [{"key": "scout", "name": "Scout", "recruit": {"equipment": "not_a_list"}}]}
        result = validate_troop_templates(data)
        assert_has_error(result, substring="expected a list")

    def test_duplicate_troop_keys(self):
        data = {"troops": [{"key": "dup", "name": "A"}, {"key": "dup", "name": "B"}]}
        result = validate_troop_templates(data)
        assert_has_error(result, substring="duplicate key 'dup'")
