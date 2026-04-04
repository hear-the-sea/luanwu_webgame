from __future__ import annotations

import pytest

from core.exceptions import ForgeOperationError
from gameplay.models import InventoryItem, ItemTemplate, PlayerTechnology
from gameplay.services.buildings import forge as forge_service
from gameplay.services.inventory.core import get_item_quantity
from gameplay.services.manor.core import ensure_manor


def _create_item_template(key: str, name: str, effect_type: str, rarity: str = "black") -> ItemTemplate:
    return ItemTemplate.objects.create(
        key=key,
        name=name,
        effect_type=effect_type,
        rarity=rarity,
        tradeable=True,
        is_usable=False,
    )


@pytest.mark.django_db
def test_get_blueprint_synthesis_options_only_shows_owned_blueprints(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="forge_bp_list", password="pass123")
    manor = ensure_manor(user)
    PlayerTechnology.objects.create(manor=manor, tech_key="forging", level=6)

    blueprint = _create_item_template("bp_qingmang", "青芒剑图纸", "tool", "blue")
    _create_item_template("equip_qingmangjian", "青芒剑", "equip_weapon", "blue")
    tong = _create_item_template("tong", "铜", "resource", "black")
    xi = _create_item_template("xi", "锡", "resource", "black")

    InventoryItem.objects.create(manor=manor, template=blueprint, quantity=2)
    InventoryItem.objects.create(manor=manor, template=tong, quantity=10)
    InventoryItem.objects.create(manor=manor, template=xi, quantity=1)

    monkeypatch.setattr(
        forge_service,
        "load_forge_blueprint_config",
        lambda: {
            "recipes": [
                {
                    "blueprint_key": "bp_qingmang",
                    "result_item_key": "equip_qingmangjian",
                    "required_forging": 5,
                    "quantity_out": 1,
                    "description": "",
                    "costs": {"tong": 5, "xi": 2},
                },
                {
                    "blueprint_key": "bp_not_owned",
                    "result_item_key": "equip_qingmangjian",
                    "required_forging": 5,
                    "quantity_out": 1,
                    "description": "",
                    "costs": {"tong": 1},
                },
            ]
        },
    )

    options = forge_service.get_blueprint_synthesis_options(manor)
    assert len(options) == 1
    option = options[0]
    assert option["blueprint_key"] == "bp_qingmang"
    assert option["blueprint_count"] == 2
    assert option["result_name"] == "青芒剑"
    assert option["max_synthesis_quantity"] == 0  # 锡不足（1/2）
    assert option["can_synthesize"] is False


@pytest.mark.django_db
def test_get_blueprint_synthesis_options_reads_latest_runtime_blueprint_config(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="forge_bp_runtime_refresh", password="pass123")
    manor = ensure_manor(user)
    PlayerTechnology.objects.create(manor=manor, tech_key="forging", level=9)

    old_blueprint = _create_item_template("bp_runtime_old", "旧图纸", "tool", "blue")
    new_blueprint = _create_item_template("bp_runtime_new", "新图纸", "tool", "purple")
    _create_item_template("equip_runtime_old", "旧产物", "equip_weapon", "blue")
    _create_item_template("equip_runtime_new", "新产物", "equip_weapon", "purple")
    tong = _create_item_template("tong", "铜", "resource", "black")

    InventoryItem.objects.create(manor=manor, template=old_blueprint, quantity=1)
    InventoryItem.objects.create(manor=manor, template=new_blueprint, quantity=1)
    InventoryItem.objects.create(manor=manor, template=tong, quantity=10)

    payload = {
        "value": {
            "recipes": [
                {
                    "blueprint_key": "bp_runtime_old",
                    "result_item_key": "equip_runtime_old",
                    "required_forging": 5,
                    "quantity_out": 1,
                    "description": "",
                    "costs": {"tong": 1},
                }
            ]
        }
    }
    monkeypatch.setattr(forge_service, "load_yaml_data", lambda *args, **kwargs: payload["value"])

    try:
        forge_service.clear_forge_blueprint_cache()
        options = forge_service.get_blueprint_synthesis_options(manor)

        assert [row["blueprint_key"] for row in options] == ["bp_runtime_old"]
        assert options[0]["result_name"] == "旧产物"

        payload["value"] = {
            "recipes": [
                {
                    "blueprint_key": "bp_runtime_new",
                    "result_item_key": "equip_runtime_new",
                    "required_forging": 7,
                    "quantity_out": 1,
                    "description": "",
                    "costs": {"tong": 2},
                }
            ]
        }

        forge_service.clear_forge_blueprint_cache()
        options = forge_service.get_blueprint_synthesis_options(manor)

        assert [row["blueprint_key"] for row in options] == ["bp_runtime_new"]
        assert options[0]["result_name"] == "新产物"
    finally:
        forge_service.clear_forge_blueprint_cache()


@pytest.mark.django_db
def test_synthesize_equipment_with_blueprint_consumes_inputs_and_grants_output(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="forge_bp_make", password="pass123")
    manor = ensure_manor(user)
    PlayerTechnology.objects.create(manor=manor, tech_key="forging", level=8)

    blueprint = _create_item_template("bp_duanma", "断马剑图纸", "tool", "purple")
    result_item = _create_item_template("equip_duanmajian", "断马剑", "equip_weapon", "blue")
    tong = _create_item_template("tong", "铜", "resource", "black")
    tie = _create_item_template("tie", "铁", "resource", "black")

    InventoryItem.objects.create(manor=manor, template=blueprint, quantity=3)
    InventoryItem.objects.create(manor=manor, template=tong, quantity=30)
    InventoryItem.objects.create(manor=manor, template=tie, quantity=12)

    monkeypatch.setattr(
        forge_service,
        "_build_blueprint_recipe_index",
        lambda: {
            "bp_duanma": {
                "blueprint_key": "bp_duanma",
                "result_item_key": "equip_duanmajian",
                "required_forging": 7,
                "quantity_out": 1,
                "description": "",
                "costs": {"tong": 10, "tie": 4},
            }
        },
    )

    result = forge_service.synthesize_equipment_with_blueprint(manor, "bp_duanma", quantity=2)
    assert result["result_key"] == result_item.key
    assert result["quantity"] == 2

    assert get_item_quantity(manor, "bp_duanma") == 1
    assert get_item_quantity(manor, "tong") == 10
    assert get_item_quantity(manor, "tie") == 4
    assert get_item_quantity(manor, "equip_duanmajian") == 2


@pytest.mark.django_db
def test_synthesize_equipment_with_blueprint_requires_forging_level(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="forge_bp_lvl", password="pass123")
    manor = ensure_manor(user)
    PlayerTechnology.objects.create(manor=manor, tech_key="forging", level=2)

    blueprint = _create_item_template("bp_need_lvl", "高阶图纸", "tool", "purple")
    _create_item_template("equip_result_lvl", "高阶装备", "equip_weapon", "purple")
    _create_item_template("tong", "铜", "resource", "black")
    InventoryItem.objects.create(manor=manor, template=blueprint, quantity=1)

    monkeypatch.setattr(
        forge_service,
        "_build_blueprint_recipe_index",
        lambda: {
            "bp_need_lvl": {
                "blueprint_key": "bp_need_lvl",
                "result_item_key": "equip_result_lvl",
                "required_forging": 5,
                "quantity_out": 1,
                "description": "",
                "costs": {"tong": 1},
            }
        },
    )

    with pytest.raises(ForgeOperationError, match="锻造技5级"):
        forge_service.synthesize_equipment_with_blueprint(manor, "bp_need_lvl", quantity=1)


@pytest.mark.django_db
def test_synthesize_equipment_with_blueprint_rejects_malformed_recipe_contract(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="forge_bp_bad_recipe", password="pass123")
    manor = ensure_manor(user)
    PlayerTechnology.objects.create(manor=manor, tech_key="forging", level=8)

    blueprint = _create_item_template("bp_bad_recipe", "坏图纸", "tool", "purple")
    _create_item_template("equip_bad_recipe_result", "坏图纸产物", "equip_weapon", "blue")
    _create_item_template("tong", "铜", "resource", "black")

    InventoryItem.objects.create(manor=manor, template=blueprint, quantity=1)

    monkeypatch.setattr(
        forge_service,
        "_build_blueprint_recipe_index",
        lambda: {
            "bp_bad_recipe": {
                "blueprint_key": "bp_bad_recipe",
                "result_item_key": "equip_bad_recipe_result",
                "required_forging": "bad",
                "quantity_out": 1,
                "description": "",
                "costs": {"tong": 1},
            }
        },
    )

    with pytest.raises(AssertionError, match="invalid forge blueprint recipe required_forging"):
        forge_service.synthesize_equipment_with_blueprint(manor, "bp_bad_recipe", quantity=1)
