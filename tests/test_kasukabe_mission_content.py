from __future__ import annotations

from pathlib import Path

import yaml

from tests.mission_reward_content_helpers import assert_fixed_reward, assert_random_group, mission_reward_payload

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
GEAR_KEYS = {
    "equip_action_mask",
    "equip_shiro_leash",
    "equip_kasukabe_satchel",
    "equip_action_beam_glove",
    "equip_nohara_pan",
}


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _item_templates() -> dict[str, dict]:
    payload = _load_yaml(DATA_DIR / "item_templates.yaml")
    return {row["key"]: row for row in payload["items"]}


def _missions() -> dict[str, dict]:
    payload = _load_yaml(DATA_DIR / "mission_templates.yaml")
    return {row["key"]: row for row in payload["missions"]}


def _original_heroes() -> dict[str, dict]:
    payload = _load_yaml(DATA_DIR / "guests" / "original.yaml")
    heroes = payload["heroes"]
    return {row["key"]: row for rarity_rows in heroes.values() for row in rarity_rows}


def test_kasukabe_crayon_recruits_shinchan_after_150_copies():
    items = _item_templates()

    crayon = items["kasukabe_crayon"]

    assert crayon["name"] == "小新的蜡笔"
    assert crayon["effect_type"] == "tool"
    assert crayon["rarity"] == "blue"
    assert crayon["is_usable"] is True
    assert crayon["effect_payload"]["action"] == "summon_guest"
    assert crayon["effect_payload"]["required_items"] == {"kasukabe_crayon": 149}
    assert crayon["effect_payload"]["exclusive_template_keys"] == ["orig_crayon_shinchan_purple"]
    assert crayon["effect_payload"]["choices"] == [{"template_key": "orig_crayon_shinchan_purple", "weight": 100}]


def test_kasukabe_blue_equipment_set_uses_fixed_bonuses():
    items = _item_templates()

    for key in GEAR_KEYS:
        item = items[key]
        payload = item["effect_payload"]
        assert item["rarity"] == "blue"
        assert item["effect_type"].startswith("equip_")
        assert payload["set_key"] == "kasukabe_defense_set"
        assert payload["set_bonus"] == [
            {"pieces": 2, "bonus": {"defense": 20, "attack": 30}},
            {"pieces": 4, "bonus": {"hp": 600, "agility": 20, "luck": 15}},
        ]


def test_kasukabe_mission_rewards_crayons_and_blue_gear():
    missions = _missions()

    mission = missions["kasukabe_daisakusen"]
    payload = mission_reward_payload(mission)

    assert mission["name"] == "春日部大作战"
    assert mission["available_weekdays"] == [2, 4, 6, 7]
    assert mission["difficulty"] == "intermediate"
    assert mission["enemy_technology"] == {"level": 7, "guest_level": 60, "guest_bonus": 0.12}
    assert_fixed_reward(payload, "kasukabe_crayon", 1)
    for chance, quantity in ((0.55, 1), (0.25, 2), (0.08, 4), (0.015, 10)):
        assert_random_group(payload, chance, quantity, {"kasukabe_crayon": 1})
    assert_random_group(
        payload,
        0.39,
        1,
        {
            "equip_action_mask": 10,
            "equip_shiro_leash": 10,
            "equip_kasukabe_satchel": 8,
            "equip_action_beam_glove": 6,
            "equip_nohara_pan": 5,
        },
    )


def test_kasukabe_guest_is_purple_military_non_pool_guest():
    heroes = _original_heroes()

    guest = heroes["orig_crayon_shinchan_purple"]

    assert guest["name"] == "蜡笔小新"
    assert guest["archetype"] == "military"
    assert guest["recruitable"] is False


def test_liangshanbo_mission_uses_configured_weekday_rotation():
    missions = _missions()

    mission = missions["liangshanbo_zhuyingtai"]

    assert mission["name"] == "梁山伯与祝英台"
    assert mission["available_weekdays"] == [1, 3, 5, 6, 7]
