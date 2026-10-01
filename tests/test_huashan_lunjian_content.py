from pathlib import Path

import yaml

from tests.mission_reward_content_helpers import assert_fixed_reward, assert_random_group, mission_reward_payload

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

EXPECTED_GUESTS = [
    ("hero_sao_di_seng", "扫地僧"),
    ("hero_zhang_sanfeng", "张三丰"),
    ("hero_dugu_qiubai", "独孤求败"),
    ("hero_dongfang_bubai", "东方不败"),
    ("hero_shi_zhongjian", "石破天"),
    ("task_huashan_huang_shang", "黄裳"),
    ("hero_a_qing", "阿青"),
    ("hero_xiaoyao_zi", "逍遥子"),
    ("hero_wang_chongyang", "王重阳"),
]

EXPECTED_GEAR_KEYS = [
    "blueprint_taotieding",
    "equip_yuanyangdao",
    "equip_shenghuoling",
    "equip_duoqinghuan",
    "equip_tulongdao",
    "equip_liekongxue",
    "equip_shuanghuagong",
    "equip_yuxiao",
    "equip_wuqiaojian",
    "equip_tiancanbaoyi",
    "equip_biyudao",
    "equip_libiegou",
    "equip_ziweiruanjian",
    "equip_xiuhuazhen",
    "equip_xuedao",
    "equip_jinshejian",
    "equip_jinshezui",
    "equip_jinlun",
    "equip_yuanyuewandao",
    "equip_tianyamingyuedao",
    "equip_kongquelin",
    "equip_xiaolifeidao",
    "equip_dagoubang",
    "equip_xuantiechongjian",
    "equip_shendiao",
    "equip_shangshanfaeling",
    "equip_ruanweijia",
    "equip_shenxingxue",
]

LEGACY_GEAR_KEYS = [
    "blueprint_taotieding",
    "equip_yuanyangdao",
    "equip_shenghuoling",
    "equip_duoqinghuan",
    "equip_tulongdao",
    "equip_tianjilu",
    "equip_tianjizhang",
    "equip_tianjiyupei",
    "equip_tianjidaopao",
    "equip_tiancanbaoyi",
    "equip_biyudao",
    "equip_libiegou",
    "equip_ziweiruanjian",
    "equip_xiuhuazhen",
    "equip_xuedao",
    "equip_jinshejian",
    "equip_jinshezui",
    "equip_jinlun",
    "equip_yuanyuewandao",
    "equip_tianyamingyuedao",
    "equip_kongquelin",
    "equip_xiaolifeidao",
    "equip_dagoubang",
    "equip_xuantiechongjian",
    "equip_shendiao",
    "equip_shangshanfaeling",
    "equip_ruanweijia",
    "equip_jinsirujia",
]


def _load_yaml(filename: str) -> dict:
    return yaml.safe_load((DATA_DIR / filename).read_text(encoding="utf-8"))


def test_huashan_lunjian_is_daily_advanced_guest_mission_with_guaranteed_chest():
    missions = {row["key"]: row for row in _load_yaml("mission_templates.yaml")["missions"]}
    mission = missions["huashan_lunjian_advanced"]

    assert mission["name"] == "华山论剑"
    assert mission["difficulty"] == "advanced"
    assert mission["guest_only"] is True
    assert mission["daily_limit"] == 1
    assert mission["base_travel_time"] == 2400
    assert mission["enemy_technology"] == {"level": 10, "guest_level": 100, "guest_bonus": 0.68}
    assert [(row["key"], row["label"]) for row in mission["enemy_guests"]] == EXPECTED_GUESTS
    assert mission["drop_table"] == {"silver": 120000, "mission_chest_huashan_lunjian_advanced": 1}


def test_wulin_chest_guarantees_one_equipment_pick_with_fifteen_percent_orange():
    items = {row["key"]: row for row in _load_yaml("item_templates.yaml")["items"]}
    chest = items["mission_chest_huashan_lunjian_advanced"]
    missions = {row["key"]: row for row in _load_yaml("mission_templates.yaml")["missions"]}
    payload = mission_reward_payload(missions["huashan_lunjian_advanced"], items)
    choices = payload["random_item_groups"][0]["choices"]

    assert chest["name"] == "华山论剑宝箱"
    assert chest["effect_type"] == "loot_box"
    assert chest["is_usable"] is True
    assert chest["storage_space"] == 5
    assert chest["tradeable"] is False
    assert_fixed_reward(payload, "experience_fruit", 185)
    assert_fixed_reward(payload, "huashan_sword_fragment", 1)
    assert_random_group(payload, 1, 1, {key: 85 if index < 18 else 27 for index, key in enumerate(EXPECTED_GEAR_KEYS)})
    assert [row["item_key"] for row in choices] == EXPECTED_GEAR_KEYS
    assert items["blueprint_taotieding"]["effect_type"] == "tool"
    recipes = _load_yaml("forge_blueprints.yaml")["recipes"]
    recipe = next(row for row in recipes if row["blueprint_key"] == "blueprint_taotieding")
    assert recipe["result_item_key"] == "equip_taotieding"
    support = payload["blueprint_material_support"]["blueprint_taotieding"]
    assert {row["item_key"]: row["min_quantity"] for row in support} == {
        key: quantity // 2 for key, quantity in recipe["costs"].items() if quantity // 2 > 0
    }
    assert all(row["min_quantity"] == row["max_quantity"] for row in support)
    assert all(items[key]["effect_type"].startswith("equip_") for key in EXPECTED_GEAR_KEYS[1:])
    assert sum(items[key]["rarity"] == "purple" for key in EXPECTED_GEAR_KEYS) == 18
    assert sum(items[key]["rarity"] == "orange" for key in EXPECTED_GEAR_KEYS) == 10
    orange_weight = sum(row["weight"] for row in choices if items[row["item_key"]]["rarity"] == "orange")
    purple_weight = sum(row["weight"] for row in choices if items[row["item_key"]]["rarity"] == "purple")
    assert orange_weight / (orange_weight + purple_weight) == 0.15


def test_legacy_wulin_chest_preserves_economy_and_original_weighted_pool():
    items = {row["key"]: row for row in _load_yaml("item_templates.yaml")["items"]}
    chest = items["huashan_wulin_chest"]
    assert chest["name"] == "武林宝箱"
    assert chest["effect_type"] == "loot_box"
    assert chest["rarity"] == "purple"
    assert chest["is_usable"] is True
    assert chest["tradeable"] is True
    assert chest["price"] == 100000
    assert chest["storage_space"] == 100
    assert chest["image"] == "mission_chest_v1.png"
    payload = chest["effect_payload"]
    assert "mission_reward_version" not in payload
    assert "mission_key" not in payload
    assert not payload.get("item_rewards")
    assert len(payload["random_item_groups"]) == 1
    assert_random_group(payload, 1, 1, {key: 5 if index < 18 else 6 for index, key in enumerate(LEGACY_GEAR_KEYS)})
    assert [row["item_key"] for row in payload["random_item_groups"][0]["choices"]] == LEGACY_GEAR_KEYS
    assert "gear_choices" not in payload
    assert "gear_chance" not in payload


def test_huang_shang_is_a_non_recruitable_huashan_boss():
    heroes_by_rarity = _load_yaml("guests/special.yaml")["heroes"]
    heroes = {row["key"]: row for rows in heroes_by_rarity.values() for row in rows}
    huang_shang = heroes["task_huashan_huang_shang"]

    assert huang_shang["name"] == "黄裳"
    assert huang_shang["recruitable"] is False
    assert huang_shang["skills"] == ["hell_instant_formation", "steadfast_planning"]
