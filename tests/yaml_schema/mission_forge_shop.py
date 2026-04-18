from __future__ import annotations

from core.utils.yaml_schema import (
    validate_forge_blueprints,
    validate_forge_equipment,
    validate_mission_templates,
    validate_shop_items,
)
from tests.yaml_schema.support import assert_has_error, assert_valid


class TestMissionTemplatesValidation:
    def test_valid_minimal(self):
        data = {"missions": [{"key": "m1", "name": "Mission 1"}]}
        assert_valid(validate_mission_templates(data))

    def test_missing_missions_key(self):
        result = validate_mission_templates({})
        assert_has_error(result, substring="missing required key 'missions'")

    def test_invalid_daily_limit(self):
        data = {"missions": [{"key": "m", "name": "M", "daily_limit": 0}]}
        result = validate_mission_templates(data)
        assert_has_error(result, substring="must be >= 1")

    def test_enemy_guests_invalid_entry(self):
        data = {"missions": [{"key": "m", "name": "M", "enemy_guests": [123]}]}
        result = validate_mission_templates(data)
        assert_has_error(result, substring="expected string or mapping")

    def test_enemy_guest_dict_missing_key(self):
        data = {"missions": [{"key": "m", "name": "M", "enemy_guests": [{"label": "Boss"}]}]}
        result = validate_mission_templates(data)
        assert_has_error(result, substring="missing required field 'key'")

    def test_enemy_troops_referential_integrity(self):
        data = {"missions": [{"key": "m", "name": "M", "enemy_troops": {"nonexistent_troop": 100}}]}
        result = validate_mission_templates(data, troop_keys={"scout", "archer"})
        assert_has_error(result, substring="not found in troop_templates.yaml")

    def test_drop_table_referential_integrity(self):
        data = {"missions": [{"key": "m", "name": "M", "drop_table": {"nonexistent_item": 1}}]}
        result = validate_mission_templates(data, item_keys={"grain", "silver"})
        assert_has_error(result, substring="not found in item_templates.yaml")

    def test_drop_table_silver_allowed(self):
        data = {"missions": [{"key": "m", "name": "M", "drop_table": {"silver": 1000}}]}
        assert_valid(validate_mission_templates(data, item_keys={"grain"}))

    def test_enemy_technology_types(self):
        data = {
            "missions": [
                {
                    "key": "m",
                    "name": "M",
                    "enemy_technology": {"level": "bad", "guest_level": 50},
                }
            ]
        }
        result = validate_mission_templates(data)
        assert_has_error(result, substring="expected int")


class TestForgeEquipmentValidation:
    def test_valid_entry(self):
        data = {
            "equipment": {
                "equip_bumao": {
                    "category": "helmet",
                    "materials": {"tong": 5},
                    "base_duration": 120,
                    "required_forging": 1,
                }
            }
        }
        assert_valid(validate_forge_equipment(data))

    def test_missing_equipment_key(self):
        result = validate_forge_equipment({})
        assert_has_error(result, substring="missing required key 'equipment'")

    def test_invalid_category(self):
        data = {"equipment": {"equip_x": {"category": "plasma_cannon"}}}
        result = validate_forge_equipment(data)
        assert_has_error(result, substring="not in allowed set")

    def test_referential_integrity(self):
        data = {"equipment": {"equip_unknown": {"category": "sword"}}}
        result = validate_forge_equipment(data, item_keys={"equip_bumao"})
        assert_has_error(result, substring="not found in item_templates.yaml")

    def test_zero_base_duration(self):
        data = {"equipment": {"equip_x": {"base_duration": 0}}}
        result = validate_forge_equipment(data)
        assert_has_error(result, substring="must be >= 1")


class TestShopItemsValidation:
    def test_valid_entry(self):
        data = {"items": [{"item_key": "grain", "stock": 1, "daily_refresh": False}]}
        assert_valid(validate_shop_items(data))

    def test_missing_items_key(self):
        result = validate_shop_items({})
        assert_has_error(result, substring="missing required key 'items'")

    def test_missing_item_key(self):
        data = {"items": [{"stock": 1}]}
        result = validate_shop_items(data)
        assert_has_error(result, substring="missing required field 'item_key'")

    def test_referential_integrity(self):
        data = {"items": [{"item_key": "nonexistent"}]}
        result = validate_shop_items(data, item_keys={"grain"})
        assert_has_error(result, substring="not found in item_templates.yaml")

    def test_wrong_type_daily_refresh(self):
        data = {"items": [{"item_key": "grain", "daily_refresh": "yes"}]}
        result = validate_shop_items(data)
        assert_has_error(result, substring="expected bool")


class TestForgeBlueprintValidation:
    def test_blueprint_key_referential_integrity(self):
        data = {
            "recipes": [
                {
                    "blueprint_key": "missing_blueprint",
                    "result_item_key": "equip_qingmangjian",
                    "required_forging": 5,
                    "quantity_out": 1,
                }
            ]
        }
        result = validate_forge_blueprints(data, item_keys={"equip_qingmangjian"})
        assert_has_error(result, substring="blueprint_key 'missing_blueprint' not found in item_templates.yaml")
