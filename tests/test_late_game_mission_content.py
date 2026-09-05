from __future__ import annotations

from pathlib import Path

import yaml

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

GUANDU_ORANGE_GEAR_KEYS = {
    "equip_taixudiguan",
    "equip_hundunlongpao",
    "equip_changshengjian",
    "equip_panlonggun",
    "equip_qiankunshengong",
    "equip_tuwujian",
    "equip_jianghuling",
}

FENGSHEN_EQUIPMENT_BOX_GEAR_KEYS = {
    "equip_hunyuansan",
    "equip_xuanmingdeng",
    "equip_zijinhulu",
    "equip_tianyinguqin",
    "equip_qixingjian",
    "equip_guiyuanzhu",
    "equip_xuanwuyufu",
    "equip_zhurixue",
}

HULAO_SCROLL_TARGETS = {
    "lvbu_guest_scroll": "hist_sljnbc_0013",
    "zhaoyun_guest_scroll": "hist_sljnbc_0012",
    "diaochan_guest_scroll": "hist_sljnbc_0425",
}

HULAO_SCROLL_RARITIES = {
    "lvbu_guest_scroll": "orange",
    "zhaoyun_guest_scroll": "purple",
    "diaochan_guest_scroll": "purple",
}


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _items() -> dict[str, dict]:
    return {row["key"]: row for row in _load_yaml(DATA_DIR / "item_templates.yaml")["items"]}


def _missions() -> dict[str, dict]:
    return {row["key"]: row for row in _load_yaml(DATA_DIR / "mission_templates.yaml")["missions"]}


def _guest_keys() -> set[str]:
    keys: set[str] = set()
    for path in sorted((DATA_DIR / "guests").glob("*.yaml")):
        payload = _load_yaml(path)
        for entries in (payload.get("heroes") or {}).values():
            keys.update(entry["key"] for entry in entries)
    return keys


def _special_heroes() -> dict[str, dict]:
    payload = _load_yaml(DATA_DIR / "guests" / "special.yaml")
    return {entry["key"]: entry for entries in (payload.get("heroes") or {}).values() for entry in entries}


def test_guandu_is_a_single_yuanshao_endgame_boss_with_orange_gear_drops():
    mission = _missions()["guandu_zhizhan"]
    items = _items()

    assert mission["difficulty"] == "advanced"
    assert mission["daily_limit"] == 1
    assert mission["base_travel_time"] == 2400
    assert mission["enemy_guests"] == [{"key": "task_guandu_yuanshao", "label": "袁绍"}]
    assert mission["enemy_technology"] == {
        "level": 10,
        "guest_level": 100,
        "guest_bonus": 0.68,
        "guest_skills": ["stratagem_burst"],
    }

    drop_table = mission["drop_table"]
    assert GUANDU_ORANGE_GEAR_KEYS <= drop_table.keys()
    assert all(items[key]["rarity"] == "orange" for key in GUANDU_ORANGE_GEAR_KEYS)
    assert all(0 < drop_table[key] < 1 for key in GUANDU_ORANGE_GEAR_KEYS)


def test_fengbang_drops_a_random_fengshen_equipment_box():
    mission = _missions()["fengbang_tianmen"]
    items = _items()
    box = items["fengshen_equipment_box"]

    assert mission["drop_table"]["fengshen_equipment_box"] == 0.08
    assert box["effect_type"] == "loot_box"
    assert box["rarity"] == "purple"
    assert box["is_usable"] is True
    assert box["effect_payload"]["gear_chance"] == 1.0
    assert {choice["item_key"] for choice in box["effect_payload"]["gear_choices"]} == FENGSHEN_EQUIPMENT_BOX_GEAR_KEYS
    assert {choice["weight"] for choice in box["effect_payload"]["gear_choices"]} == {1}
    assert (DATA_DIR / "images" / "items" / box["image"]).is_file()


def test_baimenlou_has_three_direct_recruitment_scrolls():
    mission = _missions()["baimenlou_mingyun_jueze"]
    items = _items()
    guest_keys = _guest_keys()

    assert mission["difficulty"] == "advanced"
    assert mission["daily_limit"] == 1
    assert mission["name"] == "飞将浮沉录·第三关：白门楼命运抉择"

    for scroll_key, guest_key in HULAO_SCROLL_TARGETS.items():
        scroll = items[scroll_key]
        assert guest_key in guest_keys
        assert mission["drop_table"][scroll_key] == 0.01
        assert scroll["effect_type"] == "tool"
        assert scroll["rarity"] == HULAO_SCROLL_RARITIES[scroll_key]
        assert scroll["is_usable"] is True
        assert "白门楼" in scroll["description"]
        assert "虎牢关" not in scroll["description"]
        assert scroll["effect_payload"] == {
            "action": "summon_guest",
            "exclusive_template_keys": [guest_key],
            "choices": [{"template_key": guest_key, "weight": 100}],
        }

    guest_roster = _load_yaml(DATA_DIR / "guests" / "history_sljnbc_01.yaml")["heroes"]
    lvbu = next(entry for entry in guest_roster["orange"] if entry["key"] == "hist_sljnbc_0013")
    assert lvbu["recruitable"] is False
    assert lvbu["is_world_unique"] is True
    assert lvbu["growth_range"] == [11, 14]


def test_single_bosses_use_task_only_orange_hp_profiles():
    heroes = _special_heroes()

    assert heroes["task_guandu_yuanshao"]["recruitable"] is False
    assert heroes["task_guandu_yuanshao"]["custom_stats"] == {
        "force": 322,
        "intellect": 328,
        "defense": 408,
        "agility": 238,
        "luck": 188,
        "base_hp": 7200,
    }
    assert heroes["task_hulao_lvbu"]["recruitable"] is False
    assert heroes["task_hulao_lvbu"]["custom_stats"] == {
        "force": 468,
        "intellect": 128,
        "defense": 388,
        "agility": 348,
        "luck": 158,
        "base_hp": 7600,
    }
