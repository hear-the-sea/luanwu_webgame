from types import SimpleNamespace

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.buildings import forge as forge_service
from gameplay.services.buildings.forge_helpers import (
    build_blueprint_synthesis_option,
    build_equipment_option,
    build_inventory_quantity_map,
    collect_material_keys,
    collect_recipe_related_keys,
    infer_equipment_category,
    to_decompose_category,
)
from gameplay.services.manor.core import ensure_manor


def test_infer_equipment_category_prefers_config_and_falls_back_to_effect_type():
    equipment_config = {
        "equip_cfg_sword": {"category": "sword"},
    }

    assert infer_equipment_category("equip_cfg_sword", equipment_config=equipment_config) == "sword"
    assert infer_equipment_category("equip_custom_jian", effect_type="equip_weapon") == "sword"
    assert infer_equipment_category("equip_custom_device", effect_type="equip_device") == "device"
    assert infer_equipment_category("equip_unknown", effect_type="unknown") is None


def test_to_decompose_category_merges_weapon_categories():
    assert to_decompose_category("sword") == "weapon"
    assert to_decompose_category("dao") == "weapon"
    assert to_decompose_category("helmet") == "helmet"
    assert to_decompose_category(None) is None


def test_collect_recipe_related_keys_and_inventory_quantity_map():
    recipes = [
        {"blueprint_key": "bp_a", "result_item_key": "equip_a", "costs": {"tong": 3, "tie": 1}},
        {"blueprint_key": "bp_b", "result_item_key": "equip_b", "costs": {}},
    ]
    inventory_items = [
        SimpleNamespace(template=SimpleNamespace(key="bp_a"), quantity=2),
        SimpleNamespace(template=SimpleNamespace(key="bp_a"), quantity=3),
        SimpleNamespace(template=SimpleNamespace(key="tong"), quantity=9),
    ]

    assert collect_recipe_related_keys(recipes) == {"bp_a", "equip_a", "tong", "tie", "bp_b", "equip_b"}
    assert build_inventory_quantity_map(inventory_items) == {"bp_a": 5, "tong": 9}


def test_collect_material_keys_and_build_equipment_option():
    filtered_configs = [("equip_a", {"materials": {"tong": 3, "xi": 1}}), ("equip_b", {"materials": {"tie": 2}})]
    assert collect_material_keys(filtered_configs) == {"tong", "xi", "tie"}

    option = build_equipment_option(
        "equip_a",
        {"category": "helmet", "materials": {"tong": 3, "xi": 1}, "base_duration": 120},
        item_name_map={"equip_a": "甲", "tong": "铜", "xi": "锡"},
        equipment_effect_summary_map={"equip_a": "生命+10"},
        material_quantities={"tong": 5, "xi": 0},
        material_name_fallback_map={},
        equipment_categories={"helmet": "头盔"},
        actual_duration=90,
        required_level=2,
        forging_level=1,
        max_quantity=50,
        is_forging=False,
    )

    assert option == {
        "key": "equip_a",
        "name": "甲",
        "effect_summary": "生命+10",
        "category": "helmet",
        "category_name": "头盔",
        "materials": [
            {"key": "tong", "name": "铜", "required": 3, "current": 5},
            {"key": "xi", "name": "锡", "required": 1, "current": 0},
        ],
        "base_duration": 120,
        "actual_duration": 90,
        "can_afford": False,
        "required_forging": 2,
        "is_unlocked": False,
        "max_quantity": 50,
        "is_forging": False,
    }


def test_build_blueprint_synthesis_option_calculates_affordability_and_limits():
    recipe = {
        "blueprint_key": "bp_qingmang",
        "result_item_key": "equip_qingmangjian",
        "required_forging": 5,
        "quantity_out": 1,
        "description": "蓝图描述",
        "costs": {"tong": 5, "xi": 2},
    }
    quantities = {"bp_qingmang": 2, "tong": 10, "xi": 1}
    template_map = {
        "bp_qingmang": SimpleNamespace(name="青芒剑图纸"),
        "equip_qingmangjian": SimpleNamespace(name="青芒剑", effect_type="equip_weapon"),
        "tong": SimpleNamespace(name="铜"),
        "xi": SimpleNamespace(name="锡"),
    }

    option = build_blueprint_synthesis_option(
        recipe,
        quantities=quantities,
        template_map=template_map,
        forging_level=6,
    )

    assert option == {
        "blueprint_key": "bp_qingmang",
        "blueprint_name": "青芒剑图纸",
        "blueprint_count": 2,
        "result_key": "equip_qingmangjian",
        "result_name": "青芒剑",
        "result_effect_type": "equip_weapon",
        "result_effect_summary": "",
        "result_quantity": 1,
        "required_forging": 5,
        "description": "蓝图描述",
        "costs": [
            {"key": "tong", "name": "铜", "required": 5, "current": 10},
            {"key": "xi", "name": "锡", "required": 2, "current": 1},
        ],
        "max_synthesis_quantity": 0,
        "is_unlocked": True,
        "can_afford": False,
        "can_synthesize": False,
    }


def test_build_blueprint_synthesis_option_returns_none_when_blueprint_not_owned():
    recipe = {"blueprint_key": "bp_missing", "result_item_key": "equip_x", "costs": {}}

    assert (
        build_blueprint_synthesis_option(
            recipe,
            quantities={},
            template_map={},
            forging_level=10,
        )
        is None
    )


@pytest.mark.django_db
def test_get_equipment_options_uses_single_inventory_query_for_materials(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="forge_query_user", password="pass123")
    manor = ensure_manor(user)

    ItemTemplate.objects.create(key="equip_query_helmet", name="测试头盔", effect_type="equip_helmet", rarity="green")
    tong = ItemTemplate.objects.create(key="tong", name="铜", effect_type="resource", rarity="black")
    xi = ItemTemplate.objects.create(key="xi", name="锡", effect_type="resource", rarity="black")
    InventoryItem.objects.create(manor=manor, template=tong, quantity=10)
    InventoryItem.objects.create(manor=manor, template=xi, quantity=5)

    monkeypatch.setattr(
        forge_service,
        "EQUIPMENT_CONFIG",
        {
            "equip_query_helmet": {
                "category": "helmet",
                "materials": {"tong": 3, "xi": 2},
                "base_duration": 120,
                "required_forging": 1,
            }
        },
    )
    monkeypatch.setattr(forge_service, "get_max_forging_quantity", lambda _manor: 50)
    monkeypatch.setattr(forge_service, "has_active_forging", lambda _manor: False)
    monkeypatch.setattr(forge_service, "calculate_forging_duration", lambda _base, _manor: 90)

    with CaptureQueriesContext(connection) as captured:
        options = forge_service.get_equipment_options(manor)

    inventory_queries = [
        query for query in captured.captured_queries if 'from "gameplay_inventoryitem"' in query["sql"].lower()
    ]

    assert len(options) == 1
    assert options[0]["materials"][0]["current"] == 10
    assert options[0]["materials"][1]["current"] == 5
    assert len(inventory_queries) == 1


@pytest.mark.django_db
def test_get_equipment_options_reads_latest_runtime_equipment_config(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="forge_equipment_runtime_refresh", password="pass123")
    manor = ensure_manor(user)

    ItemTemplate.objects.create(
        key="equip_runtime_old",
        name="旧运行时头盔",
        effect_type="equip_helmet",
        rarity="green",
    )
    ItemTemplate.objects.create(
        key="equip_runtime_new",
        name="新运行时衣服",
        effect_type="equip_armor",
        rarity="blue",
    )
    tong = ItemTemplate.objects.create(key="tong", name="铜", effect_type="resource", rarity="black")
    InventoryItem.objects.create(manor=manor, template=tong, quantity=10)

    payload = {
        "value": {
            "equipment": {
                "equip_runtime_old": {
                    "category": "helmet",
                    "materials": {"tong": 1},
                    "base_duration": 120,
                    "required_forging": 1,
                }
            }
        }
    }
    with monkeypatch.context() as runtime_patch:
        runtime_patch.setattr(forge_service, "load_yaml_data", lambda *args, **kwargs: payload["value"])
        runtime_patch.setattr(forge_service, "get_player_technology_level", lambda *_args, **_kwargs: 9)
        runtime_patch.setattr(forge_service, "get_max_forging_quantity", lambda _manor: 50)
        runtime_patch.setattr(forge_service, "has_active_forging", lambda _manor: False)
        runtime_patch.setattr(forge_service, "calculate_forging_duration", lambda _base, _manor: 90)

        forge_service.clear_forge_equipment_cache()
        options = forge_service.get_equipment_options(manor)
        assert [row["key"] for row in options] == ["equip_runtime_old"]
        assert options[0]["name"] == "旧运行时头盔"

        payload["value"] = {
            "equipment": {
                "equip_runtime_new": {
                    "category": "armor",
                    "materials": {"tong": 2},
                    "base_duration": 180,
                    "required_forging": 3,
                }
            }
        }

        forge_service.clear_forge_equipment_cache()
        options = forge_service.get_equipment_options(manor)
        assert [row["key"] for row in options] == ["equip_runtime_new"]
        assert options[0]["name"] == "新运行时衣服"
    forge_service.clear_forge_equipment_cache()


def test_runtime_equipment_refresh_cleanup_restores_repo_default_config(monkeypatch):
    payload = {
        "value": {
            "equipment": {
                "equip_runtime_temp": {
                    "category": "armor",
                    "materials": {"tong": 2},
                    "base_duration": 180,
                    "required_forging": 3,
                }
            }
        }
    }

    with monkeypatch.context() as runtime_patch:
        runtime_patch.setattr(forge_service, "load_yaml_data", lambda *args, **kwargs: payload["value"])
        forge_service.clear_forge_equipment_cache()
        assert set(forge_service.EQUIPMENT_CONFIG) == {"equip_runtime_temp"}

    forge_service.clear_forge_equipment_cache()
    assert "equip_bupao" in forge_service.EQUIPMENT_CONFIG
