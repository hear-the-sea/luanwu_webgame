from pathlib import Path

import yaml

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
CIVIL_BLUE_PLUS_BOOK_KEYS = {
    "book_taiyi_wind",
    "book_flower_rain",
    "book_soul_erode",
    "book_red_lotus_dance",
    "book_thunder_nine_heavens",
    "book_tangseng_sermon",
    "book_five_thunder_descent",
    "book_brahma_true_fire",
}
SAIWAI_PURPLE_GEAR_KEYS = {
    "equip_xuanwukui",
    "equip_xuanwujia",
    "equip_xuanwuxue",
    "equip_xuanwuchui",
    "equip_jinlun",
    "equip_jinshezui",
    "equip_tiancanbaoyi",
}
SAIWAI_ORANGE_GEAR_KEYS = {"equip_jinsirujia", "equip_hanxuebaoma"}


def _load_yaml(filename: str) -> dict:
    return yaml.safe_load((DATA_DIR / filename).read_text(encoding="utf-8"))


def test_manzu_invasion_drops_civil_skill_book_box_at_configured_rate():
    missions = {row["key"]: row for row in _load_yaml("mission_templates.yaml")["missions"]}

    assert missions["manzu_ruqin"]["drop_table"]["civil_skill_book_box"] == 0.15


def test_zhuiji_manzu_uses_chase_themed_rewards():
    missions = {row["key"]: row for row in _load_yaml("mission_templates.yaml")["missions"]}
    drop_table = missions["zhuiji_manzu"]["drop_table"]

    assert drop_table["silver"] == 40000
    assert drop_table["experience_pineapple"] == {"chance": 0.8, "count": 10}
    assert drop_table["saiwai_equipment_box"] == 0.2
    assert drop_table["book_dragon_roar"] == 0.005


def test_saiwai_equipment_box_is_purple_with_a_rare_orange_gear_layer():
    items = {row["key"]: row for row in _load_yaml("item_templates.yaml")["items"]}
    box = items["saiwai_equipment_box"]
    choices = box["effect_payload"]["gear_choices"]
    choice_keys = {choice["item_key"] for choice in choices}

    assert box["effect_type"] == "loot_box"
    assert box["rarity"] == "purple"
    assert box["effect_payload"]["gear_chance"] == 1
    assert choice_keys == SAIWAI_PURPLE_GEAR_KEYS | SAIWAI_ORANGE_GEAR_KEYS
    assert "equip_xuanwujigui" not in choice_keys
    assert {choice["weight"] for choice in choices if choice["item_key"] in SAIWAI_PURPLE_GEAR_KEYS} == {10}
    assert {choice["weight"] for choice in choices if choice["item_key"] in SAIWAI_ORANGE_GEAR_KEYS} == {2}
    assert all(items[key]["rarity"] in {"purple", "orange"} for key in choice_keys)
    assert (DATA_DIR / "images" / "items" / box["image"]).is_file()


def test_civil_skill_book_box_contains_only_blue_or_better_civil_books():
    items = {row["key"]: row for row in _load_yaml("item_templates.yaml")["items"]}
    box = items["civil_skill_book_box"]
    choices = box["effect_payload"]["skill_book_choices"]
    choice_keys = {choice["item_key"] for choice in choices}

    assert box["effect_type"] == "loot_box"
    assert box["effect_payload"]["skill_book_chance"] == 1
    assert choice_keys == CIVIL_BLUE_PLUS_BOOK_KEYS
    assert {choice["weight"] for choice in choices} == {1}
    assert all(items[key]["effect_type"] == "skill_book" for key in choice_keys)
    assert all(items[key]["rarity"] in {"blue", "purple", "orange"} for key in choice_keys)
    assert (DATA_DIR / "images" / "items" / box["image"]).is_file()
