from pathlib import Path

import yaml

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
    "equip_taotieding",
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
    assert mission["drop_table"]["huashan_wulin_chest"] == 1


def test_wulin_chest_contains_exactly_the_requested_equal_weight_equipment_pool():
    items = {row["key"]: row for row in _load_yaml("item_templates.yaml")["items"]}
    chest = items["huashan_wulin_chest"]
    choices = chest["effect_payload"]["gear_choices"]

    assert chest["name"] == "武林宝箱"
    assert chest["effect_type"] == "loot_box"
    assert chest["is_usable"] is True
    assert chest["effect_payload"]["gear_chance"] == 1
    assert [row["item_key"] for row in choices] == EXPECTED_GEAR_KEYS
    assert all(row["weight"] == 1 for row in choices)
    assert all(items[key]["effect_type"].startswith("equip_") for key in EXPECTED_GEAR_KEYS)
    assert sum(items[key]["rarity"] == "purple" for key in EXPECTED_GEAR_KEYS) == 18
    assert sum(items[key]["rarity"] == "orange" for key in EXPECTED_GEAR_KEYS) == 10


def test_huang_shang_is_a_non_recruitable_huashan_boss():
    heroes_by_rarity = _load_yaml("guests/special.yaml")["heroes"]
    heroes = {row["key"]: row for rows in heroes_by_rarity.values() for row in rows}
    huang_shang = heroes["task_huashan_huang_shang"]

    assert huang_shang["name"] == "黄裳"
    assert huang_shang["recruitable"] is False
    assert huang_shang["skills"] == ["hell_instant_formation", "steadfast_planning"]
