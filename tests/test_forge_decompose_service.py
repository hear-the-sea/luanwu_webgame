from __future__ import annotations

import pytest

from core.exceptions import ForgeOperationError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.buildings import forge as forge_service
from gameplay.services.inventory.core import get_item_quantity
from gameplay.services.manor.core import ensure_manor


def _create_item_template(
    key: str,
    name: str,
    effect_type: str,
    rarity: str = "black",
    *,
    effect_payload: dict | None = None,
) -> ItemTemplate:
    return ItemTemplate.objects.create(
        key=key,
        name=name,
        effect_type=effect_type,
        effect_payload=effect_payload or {},
        rarity=rarity,
        tradeable=True,
        is_usable=False,
    )


@pytest.mark.django_db
def test_get_decomposable_equipment_options_filters_by_rules(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="forge_filter", password="pass123")
    manor = ensure_manor(user)

    equip_green = _create_item_template("equip_green_ok", "可分解绿装", "equip_weapon", "green")
    equip_recruit = _create_item_template("equip_recruit_only", "募兵专用", "equip_weapon", "blue")
    _create_item_template("equip_black_skip", "黑装", "equip_weapon", "black")
    _create_item_template("tool_green_skip", "绿色道具", "tool", "green")

    InventoryItem.objects.create(manor=manor, template=equip_green, quantity=3)
    InventoryItem.objects.create(manor=manor, template=equip_recruit, quantity=2)
    InventoryItem.objects.create(
        manor=manor,
        template=ItemTemplate.objects.get(key="equip_black_skip"),
        quantity=2,
    )
    InventoryItem.objects.create(
        manor=manor,
        template=ItemTemplate.objects.get(key="tool_green_skip"),
        quantity=2,
    )

    monkeypatch.setattr(forge_service, "get_recruitment_equipment_keys", lambda: {"equip_recruit_only"})

    options = forge_service.get_decomposable_equipment_options(manor)

    assert len(options) == 1
    assert options[0]["key"] == "equip_green_ok"
    assert options[0]["quantity"] == 3
    assert options[0]["rarity"] == "green"


@pytest.mark.django_db
def test_get_decomposable_equipment_options_category_fallback_from_effect_type(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="forge_category_fallback", password="pass123")
    manor = ensure_manor(user)

    equip_custom_helmet = _create_item_template("equip_custom_helmet", "自定义头盔", "equip_helmet", "green")
    InventoryItem.objects.create(manor=manor, template=equip_custom_helmet, quantity=2)

    monkeypatch.setattr(forge_service, "get_recruitment_equipment_keys", lambda: set())

    options = forge_service.get_decomposable_equipment_options(manor, category="helmet")

    assert len(options) == 1
    assert options[0]["key"] == "equip_custom_helmet"
    assert options[0]["category"] == "helmet"


@pytest.mark.django_db
def test_get_decomposable_equipment_options_merges_weapon_categories(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="forge_weapon_group", password="pass123")
    manor = ensure_manor(user)

    sword = _create_item_template("equip_custom_jian", "自定义剑", "equip_weapon", "green")
    dao = _create_item_template("equip_custom_dao", "自定义刀", "equip_weapon", "green")
    spear = _create_item_template("equip_custom_qiang", "自定义枪", "equip_weapon", "green")
    InventoryItem.objects.create(manor=manor, template=sword, quantity=1)
    InventoryItem.objects.create(manor=manor, template=dao, quantity=1)
    InventoryItem.objects.create(manor=manor, template=spear, quantity=1)

    monkeypatch.setattr(forge_service, "get_recruitment_equipment_keys", lambda: set())

    options = forge_service.get_decomposable_equipment_options(manor, category="weapon")

    assert len(options) == 3
    assert {row["key"] for row in options} == {"equip_custom_jian", "equip_custom_dao", "equip_custom_qiang"}
    assert {row["category"] for row in options} == {"weapon"}


@pytest.mark.django_db
def test_get_decomposable_equipment_options_supports_device_category(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="forge_device_group", password="pass123")
    manor = ensure_manor(user)

    device = _create_item_template(
        "equip_custom_device",
        "自定义器械",
        "equip_device",
        "green",
        effect_payload={"troop_stat_bonus": {"gong": {"hp_pct": 0.01}}},
    )
    InventoryItem.objects.create(manor=manor, template=device, quantity=1)

    monkeypatch.setattr(forge_service, "get_recruitment_equipment_keys", lambda: set())

    device_options = forge_service.get_decomposable_equipment_options(manor, category="device")

    assert len(device_options) == 1
    assert device_options[0]["key"] == "equip_custom_device"
    assert device_options[0]["category"] == "device"
    assert device_options[0]["effect_summary"] == "弓系生命+1%"


@pytest.mark.django_db
def test_get_decomposable_equipment_options_reads_latest_runtime_decompose_config(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="forge_decompose_runtime_refresh", password="pass123")
    manor = ensure_manor(user)

    green = _create_item_template("equip_runtime_green", "运行时绿装", "equip_weapon", "green")
    blue = _create_item_template("equip_runtime_blue", "运行时蓝装", "equip_weapon", "blue")
    InventoryItem.objects.create(manor=manor, template=green, quantity=1)
    InventoryItem.objects.create(manor=manor, template=blue, quantity=1)

    payload = {
        "value": {
            "supported_rarities": ["green"],
            "rarity_labels": {"green": "绿色", "blue": "蓝色"},
            "rarity_order": {"green": 1, "blue": 2},
            "base_materials": {
                "green": {"tong": [1, 1]},
                "blue": {"tong": [2, 2]},
            },
            "chance_rewards": {
                "green": {"wood_essence": 1.0},
                "blue": {"wood_essence": 1.0},
            },
        }
    }
    monkeypatch.setattr(forge_service, "load_yaml_data", lambda *args, **kwargs: payload["value"])
    monkeypatch.setattr(forge_service, "get_recruitment_equipment_keys", lambda: set())

    try:
        forge_service.clear_forge_decompose_cache()
        options = forge_service.get_decomposable_equipment_options(manor)
        assert [row["key"] for row in options] == ["equip_runtime_green"]

        payload["value"] = {
            "supported_rarities": ["blue"],
            "rarity_labels": {"green": "绿色", "blue": "蓝色"},
            "rarity_order": {"green": 1, "blue": 2},
            "base_materials": {
                "green": {"tong": [1, 1]},
                "blue": {"tong": [2, 2]},
            },
            "chance_rewards": {
                "green": {"wood_essence": 1.0},
                "blue": {"wood_essence": 1.0},
            },
        }

        forge_service.clear_forge_decompose_cache()
        options = forge_service.get_decomposable_equipment_options(manor)
        assert [row["key"] for row in options] == ["equip_runtime_blue"]
    finally:
        forge_service.clear_forge_decompose_cache()


@pytest.mark.django_db
def test_decompose_equipment_consumes_gear_and_grants_rewards(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="forge_decompose", password="pass123")
    manor = ensure_manor(user)

    equip = _create_item_template("equip_green_decompose", "待分解绿装", "equip_weapon", "green")
    for key, name in [
        ("tong", "铜"),
        ("xi", "锡"),
        ("tie", "铁"),
        ("wood_essence", "木质精华"),
        ("copper_essence", "铜质精华"),
        ("shuiqumu", "水曲木"),
        ("paozi", "刨子"),
        ("zaozi", "凿子"),
        ("tiemu", "铁木"),
        ("zitanmu", "紫檀木"),
        ("heiyuanshi", "黑原石"),
        ("jingangmei", "金刚煤"),
    ]:
        _create_item_template(key, name, "resource", "black")

    InventoryItem.objects.create(manor=manor, template=equip, quantity=2)

    monkeypatch.setattr(forge_service, "get_recruitment_equipment_keys", lambda: set())
    monkeypatch.setattr("gameplay.services.buildings.forge.random.randint", lambda a, b: a)
    monkeypatch.setattr("gameplay.services.buildings.forge.random.random", lambda: 0.0)

    result = forge_service.decompose_equipment(manor, "equip_green_decompose", quantity=2)

    assert result["equipment_key"] == "equip_green_decompose"
    assert result["quantity"] == 2
    # green 配置：tong(2,5), xi(1,3), tie(1,2)；random.randint 固定取最小值
    assert result["rewards"]["tong"] == 4
    assert result["rewards"]["xi"] == 2
    assert result["rewards"]["tie"] == 2
    # random.random=0 时，绿色概率奖励全部触发
    assert result["rewards"]["wood_essence"] == 2
    assert result["rewards"]["copper_essence"] == 2
    for material_key in ["shuiqumu", "paozi", "zaozi", "tiemu", "zitanmu", "heiyuanshi", "jingangmei"]:
        assert result["rewards"][material_key] == 2

    assert get_item_quantity(manor, "equip_green_decompose") == 0
    assert get_item_quantity(manor, "tong") == 4
    assert get_item_quantity(manor, "xi") == 2
    assert get_item_quantity(manor, "tie") == 2
    assert get_item_quantity(manor, "wood_essence") == 2
    assert get_item_quantity(manor, "copper_essence") == 2
    for material_key in ["shuiqumu", "paozi", "zaozi", "tiemu", "zitanmu", "heiyuanshi", "jingangmei"]:
        assert get_item_quantity(manor, material_key) == 2


@pytest.mark.django_db
def test_decompose_equipment_rejects_recruitment_gear(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="forge_recruit_block", password="pass123")
    manor = ensure_manor(user)

    equip = _create_item_template("equip_recruit_blocked", "募兵装备", "equip_weapon", "blue")
    InventoryItem.objects.create(manor=manor, template=equip, quantity=1)

    monkeypatch.setattr(forge_service, "get_recruitment_equipment_keys", lambda: {"equip_recruit_blocked"})

    with pytest.raises(ForgeOperationError, match="招募护院"):
        forge_service.decompose_equipment(manor, "equip_recruit_blocked", quantity=1)

    assert get_item_quantity(manor, "equip_recruit_blocked") == 1


@pytest.mark.django_db
def test_decompose_equipment_rejects_malformed_reward_contract(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="forge_bad_rewards", password="pass123")
    manor = ensure_manor(user)

    equip = _create_item_template("equip_bad_rewards", "坏奖励绿装", "equip_weapon", "green")
    _create_item_template("tong", "铜", "resource", "black")
    InventoryItem.objects.create(manor=manor, template=equip, quantity=1)

    monkeypatch.setattr(forge_service, "get_recruitment_equipment_keys", lambda: set())
    monkeypatch.setattr(
        forge_service,
        "_roll_decompose_rewards",
        lambda *_args, **_kwargs: {"tong": "bad"},
    )

    with pytest.raises(AssertionError, match="invalid forge decompose rewards amount"):
        forge_service.decompose_equipment(manor, "equip_bad_rewards", quantity=1)

    assert get_item_quantity(manor, "equip_bad_rewards") == 1


def test_decompose_forge_material_probabilities_match_balance():
    forge_service.clear_forge_decompose_cache()
    config = forge_service.load_forge_decompose_config()
    expected = {
        "green": {
            "shuiqumu": 0.25,
            "paozi": 0.12,
            "zaozi": 0.12,
            "tiemu": 0.06,
            "zitanmu": 0.05,
            "heiyuanshi": 0.06,
            "jingangmei": 0.04,
        },
        "blue": {
            "shuiqumu": 0.35,
            "paozi": 0.20,
            "zaozi": 0.20,
            "tiemu": 0.14,
            "zitanmu": 0.12,
            "heiyuanshi": 0.15,
            "jingangmei": 0.10,
            "gaolu": 0.02,
        },
        "purple": {
            "shuiqumu": 0.50,
            "paozi": 0.30,
            "zaozi": 0.30,
            "tiemu": 0.24,
            "zitanmu": 0.22,
            "heiyuanshi": 0.26,
            "jingangmei": 0.20,
            "gaolu": 0.06,
        },
        "orange": {
            "shuiqumu": 0.65,
            "paozi": 0.42,
            "zaozi": 0.42,
            "tiemu": 0.36,
            "zitanmu": 0.34,
            "heiyuanshi": 0.40,
            "jingangmei": 0.32,
            "gaolu": 0.14,
        },
    }

    for rarity, expected_rewards in expected.items():
        rewards = config["chance_rewards"][rarity]
        actual = {key: rewards[key] for key in expected_rewards if key in rewards}
        assert actual == expected_rewards, rarity
        assert "chunqiu_coin" not in rewards
        assert "chunqiu_coin" not in config["base_materials"][rarity]


def test_decompose_probabilities_increase_for_higher_rarity():
    forge_service.clear_forge_decompose_cache()
    config = forge_service.load_forge_decompose_config()
    blue = config["chance_rewards"]["blue"]
    purple = config["chance_rewards"]["purple"]
    orange = config["chance_rewards"]["orange"]

    for reward_key in [
        "wood_essence",
        "copper_essence",
        "xuan_tie_essence",
        "air_stone",
        "fire_stone",
        "earth_stone",
        "water_stone",
    ]:
        assert purple[reward_key] >= blue[reward_key]
        assert orange[reward_key] >= purple[reward_key]

    for reward_key in [
        "shuiqumu",
        "paozi",
        "zaozi",
        "tiemu",
        "zitanmu",
        "heiyuanshi",
        "jingangmei",
        "gaolu",
    ]:
        probabilities = [
            config["chance_rewards"][rarity].get(reward_key, 0.0) for rarity in config["supported_rarities"]
        ]
        assert probabilities == sorted(probabilities), reward_key


def test_get_recruitment_equipment_keys_does_not_fail_open(monkeypatch):
    monkeypatch.setattr(
        "gameplay.services.buildings.forge.load_troop_templates", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    with pytest.raises(RuntimeError, match="boom"):
        forge_service.get_recruitment_equipment_keys.__wrapped__()
