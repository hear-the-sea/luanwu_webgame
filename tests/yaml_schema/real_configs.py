from __future__ import annotations

from pathlib import Path

import pytest

from core.utils.yaml_schema import (
    validate_all_configs,
    validate_arena_rules,
    validate_auction_items,
    validate_building_templates,
    validate_forge_blueprints,
    validate_forge_equipment,
    validate_guest_skills,
    validate_guest_templates,
    validate_item_templates,
    validate_mission_templates,
    validate_shop_items,
    validate_trade_market_rules,
    validate_troop_templates,
)
from tests.yaml_schema.support import assert_valid


class TestRealConfigsPassValidation:
    @pytest.fixture
    def data_dir(self):
        return Path(__file__).resolve().parents[2] / "data"

    def test_item_templates_valid(self, data_dir):
        import yaml

        with (data_dir / "item_templates.yaml").open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
        assert_valid(validate_item_templates(data))

    def test_building_templates_valid(self, data_dir):
        import yaml

        with (data_dir / "building_templates.yaml").open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
        assert_valid(validate_building_templates(data))

    def test_guest_templates_valid(self, data_dir):
        import yaml

        with (data_dir / "guest_templates.yaml").open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
        assert_valid(validate_guest_templates(data))

    def test_guest_skills_valid(self, data_dir):
        import yaml

        with (data_dir / "guest_skills.yaml").open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
        assert_valid(validate_guest_skills(data))

    def test_troop_templates_valid(self, data_dir):
        import yaml

        with (data_dir / "troop_templates.yaml").open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
        assert_valid(validate_troop_templates(data))

    def test_mission_templates_valid(self, data_dir):
        import yaml

        with (data_dir / "mission_templates.yaml").open("r", encoding="utf-8") as handle:
            mission_data = yaml.safe_load(handle)
        with (data_dir / "item_templates.yaml").open("r", encoding="utf-8") as handle:
            item_data = yaml.safe_load(handle)
        with (data_dir / "troop_templates.yaml").open("r", encoding="utf-8") as handle:
            troop_data = yaml.safe_load(handle)

        item_keys = {item["key"] for item in item_data.get("items", []) if isinstance(item, dict) and "key" in item}
        troop_keys = {
            troop["key"] for troop in troop_data.get("troops", []) if isinstance(troop, dict) and "key" in troop
        }
        assert_valid(validate_mission_templates(mission_data, item_keys=item_keys, troop_keys=troop_keys))

    def test_forge_equipment_valid(self, data_dir):
        import yaml

        with (data_dir / "forge_equipment.yaml").open("r", encoding="utf-8") as handle:
            forge_data = yaml.safe_load(handle)
        with (data_dir / "item_templates.yaml").open("r", encoding="utf-8") as handle:
            item_data = yaml.safe_load(handle)

        item_keys = {item["key"] for item in item_data.get("items", []) if isinstance(item, dict) and "key" in item}
        assert_valid(validate_forge_equipment(forge_data, item_keys=item_keys))

    def test_forge_blueprints_valid(self, data_dir):
        import yaml

        with (data_dir / "forge_blueprints.yaml").open("r", encoding="utf-8") as handle:
            forge_data = yaml.safe_load(handle)
        with (data_dir / "item_templates.yaml").open("r", encoding="utf-8") as handle:
            item_data = yaml.safe_load(handle)

        item_keys = {item["key"] for item in item_data.get("items", []) if isinstance(item, dict) and "key" in item}
        assert_valid(validate_forge_blueprints(forge_data, item_keys=item_keys))

    def test_core_set_blueprints_stay_wired_across_configs(self, data_dir):
        import yaml

        tracked_blueprints = {
            "blueprint_xiaoweitoukie",
            "blueprint_xiaoweikaijia",
            "blueprint_xiaoweichangxue",
            "blueprint_xiaoweichangjian",
            "blueprint_qinglongkui",
            "blueprint_qinglongjia",
            "blueprint_qinglongxue",
            "blueprint_qinglongdao",
            "blueprint_qilinkui",
            "blueprint_qilinjia",
            "blueprint_qilinxue",
            "blueprint_qilindao",
            "blueprint_xuanwukui",
            "blueprint_xuanwujia",
            "blueprint_xuanwuxue",
            "blueprint_xuanwuchui",
            "blueprint_fenglingguan",
            "blueprint_fenghuangyu",
            "blueprint_fenglingshan",
            "blueprint_huxianpao",
            "blueprint_huxianxie",
            "blueprint_huxianjian",
        }

        with (data_dir / "forge_blueprints.yaml").open("r", encoding="utf-8") as handle:
            forge_data = yaml.safe_load(handle)
        with (data_dir / "item_templates.yaml").open("r", encoding="utf-8") as handle:
            item_data = yaml.safe_load(handle)

        item_keys = {item["key"] for item in item_data.get("items", []) if isinstance(item, dict) and "key" in item}
        blueprint_keys = {
            recipe["blueprint_key"]
            for recipe in forge_data.get("recipes", [])
            if isinstance(recipe, dict) and "blueprint_key" in recipe
        }

        assert tracked_blueprints <= item_keys
        assert tracked_blueprints <= blueprint_keys

    def test_device_blueprints_stay_wired_across_configs(self, data_dir):
        import yaml

        tracked_blueprints = {
            "blueprint_xiaoxingjiguanshu",
            "blueprint_tongchanjiguan",
            "blueprint_jixiemao",
            "blueprint_jiguanchuniao",
            "blueprint_kuileimuren",
            "blueprint_qingji",
            "blueprint_taotieding",
            "blueprint_jiguanxiong",
            "blueprint_muniuliuma",
            "blueprint_kuileiren",
            "blueprint_xuanwujigui",
            "blueprint_feiyuan",
            "blueprint_chixiaojifeng",
            "blueprint_mojiajiguanren",
        }

        with (data_dir / "forge_blueprints.yaml").open("r", encoding="utf-8") as handle:
            forge_data = yaml.safe_load(handle)
        with (data_dir / "item_templates.yaml").open("r", encoding="utf-8") as handle:
            item_data = yaml.safe_load(handle)

        item_keys = {item["key"] for item in item_data.get("items", []) if isinstance(item, dict) and "key" in item}
        blueprint_keys = {
            recipe["blueprint_key"]
            for recipe in forge_data.get("recipes", [])
            if isinstance(recipe, dict) and "blueprint_key" in recipe
        }

        assert tracked_blueprints <= item_keys
        assert tracked_blueprints <= blueprint_keys

    def test_legacy_green_weapon_blueprints_are_removed(self, data_dir):
        import yaml

        removed_blueprints = {
            "blueprint_qingmangjian",
            "blueprint_duanmajian",
            "blueprint_mingshejiebian",
        }

        with (data_dir / "forge_blueprints.yaml").open("r", encoding="utf-8") as handle:
            forge_data = yaml.safe_load(handle)
        with (data_dir / "item_templates.yaml").open("r", encoding="utf-8") as handle:
            item_data = yaml.safe_load(handle)

        item_keys = {item["key"] for item in item_data.get("items", []) if isinstance(item, dict) and "key" in item}
        blueprint_keys = {
            recipe["blueprint_key"]
            for recipe in forge_data.get("recipes", [])
            if isinstance(recipe, dict) and "blueprint_key" in recipe
        }

        assert removed_blueprints.isdisjoint(item_keys)
        assert removed_blueprints.isdisjoint(blueprint_keys)

    def test_shop_items_valid(self, data_dir):
        import yaml

        with (data_dir / "shop_items.yaml").open("r", encoding="utf-8") as handle:
            shop_data = yaml.safe_load(handle)
        with (data_dir / "item_templates.yaml").open("r", encoding="utf-8") as handle:
            item_data = yaml.safe_load(handle)

        item_keys = {item["key"] for item in item_data.get("items", []) if isinstance(item, dict) and "key" in item}
        assert_valid(validate_shop_items(shop_data, item_keys=item_keys))

    def test_new_skill_books_stay_wired_across_configs(self, data_dir):
        import yaml

        tracked_skill_books = {
            "book_prison_break_blade",
            "book_city_felling_strike",
            "book_fatal_chain_sword",
            "book_meteor_pierce_moon",
            "book_hell_instant_formation",
            "book_steadfast_planning",
            "book_iron_wall_heart",
            "book_last_chance_revival",
            "book_draw_enemy_blades",
            "book_desperate_beast",
            "book_bloodthirsty_fury",
            "book_comrade_command",
        }

        with (data_dir / "item_templates.yaml").open("r", encoding="utf-8") as handle:
            item_data = yaml.safe_load(handle)
        with (data_dir / "shop_items.yaml").open("r", encoding="utf-8") as handle:
            shop_data = yaml.safe_load(handle)
        with (data_dir / "auction_items.yaml").open("r", encoding="utf-8") as handle:
            auction_data = yaml.safe_load(handle)
        with (data_dir / "guest_skills.yaml").open("r", encoding="utf-8") as handle:
            skill_data = yaml.safe_load(handle)

        item_keys = {item["key"] for item in item_data.get("items", []) if isinstance(item, dict) and "key" in item}
        skill_keys = {
            skill["key"] for skill in skill_data.get("skills", []) if isinstance(skill, dict) and "key" in skill
        }
        shop_item_keys = {
            item["item_key"] for item in shop_data.get("items", []) if isinstance(item, dict) and "item_key" in item
        }
        auction_item_keys = {
            item["item_key"] for item in auction_data.get("items", []) if isinstance(item, dict) and "item_key" in item
        }
        tracked_book_skill_keys = {
            item.get("effect_payload", {}).get("skill_key")
            for item in item_data.get("items", [])
            if isinstance(item, dict) and item.get("key") in tracked_skill_books
        }

        assert tracked_skill_books <= item_keys
        assert tracked_book_skill_keys <= skill_keys
        assert tracked_skill_books.isdisjoint(shop_item_keys)
        assert {
            "book_prison_break_blade",
            "book_city_felling_strike",
            "book_hell_instant_formation",
            "book_desperate_beast",
            "book_bloodthirsty_fury",
            "book_comrade_command",
        } <= auction_item_keys
        assert_valid(validate_auction_items(auction_data, item_keys=item_keys))

    def test_arena_rules_valid(self, data_dir):
        import yaml

        with (data_dir / "arena_rules.yaml").open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
        assert_valid(validate_arena_rules(data))

    def test_trade_market_rules_valid(self, data_dir):
        import yaml

        with (data_dir / "trade_market_rules.yaml").open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
        assert_valid(validate_trade_market_rules(data))

    def test_validate_all_configs(self, data_dir):
        result = validate_all_configs(data_dir)
        assert_valid(result)
