from __future__ import annotations

from pathlib import Path

import pytest
import yaml

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

GREEN_GEAR_KEYS = [
    "equip_yupei",
    "equip_fudai",
    "equip_taomujian",
    "equip_tongjing",
    "equip_nichangyuyi",
    "equip_zhuyelu",
]
BLUE_GEAR_KEYS = [
    "equip_hanyuejian",
    "equip_xuanbinggong",
    "equip_xingluoshan",
    "equip_xingshashouchuan",
    "equip_liuyunpei",
    "equip_guanxingdeng",
]
GREEN_SKILL_BOOK_KEYS = [
    "book_ice_curse",
    "book_thunder_control",
    "book_dragon_break_curse",
    "book_endless_chatter",
    "book_steadfast_planning",
    "book_iron_wall_heart",
]
BLUE_SKILL_BOOK_KEYS = [
    "book_meteor_pierce_moon",
    "book_taiyi_wind",
    "book_flower_rain",
    "book_soul_erode",
    "book_red_lotus_dance",
    "book_fatal_chain_sword",
]


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _items() -> dict[str, dict]:
    payload = _load_yaml(DATA_DIR / "item_templates.yaml")
    return {row["key"]: row for row in payload["items"]}


def _missions() -> dict[str, dict]:
    payload = _load_yaml(DATA_DIR / "mission_templates.yaml")
    return {row["key"]: row for row in payload["missions"]}


def _weighted_choices(keys: list[str]) -> list[dict[str, int | str]]:
    return [{"item_key": key, "weight": 1} for key in keys]


def test_mid_autumn_mission_contract():
    mission = _missions()["mid_autumn_moon_palace_trial"]

    assert mission["name"] == "广寒宫试炼"
    assert mission["difficulty"] == "intermediate"
    assert mission["available_weekdays"] == [2, 4, 6, 7]
    assert mission["daily_limit"] == 3
    assert mission["base_travel_time"] == 1800
    assert mission["battle_type"] == "task1"
    assert [guest["key"] for guest in mission["enemy_guests"]] == [
        "myth_moon_jade_rabbit",
        "myth_moon_wu_gang",
        "hist_xianqin_0413",
    ]
    assert mission["enemy_troops"] == {
        "quan_wang": 300,
        "jian_hao": 300,
        "divine_archer": 200,
    }
    assert mission["enemy_technology"] == {
        "level": 7,
        "guest_level": 60,
        "guest_bonus": 0.12,
    }
    assert mission["drop_table"] == {
        "silver": 40000,
        "mid_autumn_mooncake": 1,
        "mid_autumn_mooncake_bonus_1": {
            "chance": 0.55,
            "count": 1,
            "choices": ["mid_autumn_mooncake"],
        },
        "mid_autumn_mooncake_bonus_2": {
            "chance": 0.25,
            "count": 2,
            "choices": ["mid_autumn_mooncake"],
        },
        "mid_autumn_mooncake_bonus_4": {
            "chance": 0.08,
            "count": 4,
            "choices": ["mid_autumn_mooncake"],
        },
        "mid_autumn_mooncake_bonus_10": {
            "chance": 0.015,
            "count": 10,
            "choices": ["mid_autumn_mooncake"],
        },
        "jade_rabbit_summon_scroll": {"chance": 0.08, "count": 1},
        "wu_gang_summon_scroll": {"chance": 0.05, "count": 1},
        "chang_e_summon_scroll": {"chance": 0.03, "count": 1},
    }


def test_mid_autumn_mooncake_contract():
    mooncake = _items()["mid_autumn_mooncake"]

    assert mooncake["name"] == "月饼"
    assert mooncake["effect_type"] == "loot_box"
    assert mooncake["rarity"] == "green"
    assert mooncake["tradeable"] is True
    assert mooncake["is_usable"] is True
    assert mooncake["image"] == "yuebing.png"
    payload = mooncake["effect_payload"]
    assert payload["resources"] == {"grain": 2000}
    assert payload["silver_min"] == 8000
    assert payload["silver_max"] == 15000
    assert payload["item_rewards"] == [
        {"item_key": "zhixuesan", "min_quantity": 2, "max_quantity": 5},
        {"item_key": "buxuedan", "min_quantity": 1, "max_quantity": 3},
        {"item_key": "experience_fruit", "min_quantity": 3, "max_quantity": 8},
    ]
    assert payload["random_item_groups"] == [
        {
            "chance": 0.60,
            "min_quantity": 1,
            "max_quantity": 2,
            "choices": _weighted_choices(["baicaodan"]),
        },
        {
            "chance": 0.35,
            "min_quantity": 1,
            "max_quantity": 2,
            "choices": _weighted_choices(["experience_peach"]),
        },
        {
            "chance": 0.25,
            "min_quantity": 1,
            "max_quantity": 1,
            "choices": _weighted_choices(["fangdajing"]),
        },
        {
            "chance": 0.25,
            "min_quantity": 1,
            "max_quantity": 2,
            "choices": _weighted_choices(["haorenka"]),
        },
        {
            "chance": 0.08,
            "min_quantity": 1,
            "max_quantity": 1,
            "choices": _weighted_choices(["experience_pineapple"]),
        },
        {
            "chance": 0.05,
            "min_quantity": 1,
            "max_quantity": 1,
            "choices": _weighted_choices(["mission_card"]),
        },
        {
            "chance": 0.07,
            "min_quantity": 1,
            "max_quantity": 1,
            "choices": _weighted_choices(GREEN_GEAR_KEYS),
        },
        {
            "chance": 0.04,
            "min_quantity": 1,
            "max_quantity": 1,
            "choices": _weighted_choices(GREEN_SKILL_BOOK_KEYS),
        },
        {
            "chance": 0.03,
            "min_quantity": 1,
            "max_quantity": 1,
            "choices": _weighted_choices(BLUE_GEAR_KEYS),
        },
        {
            "chance": 0.02,
            "min_quantity": 1,
            "max_quantity": 1,
            "choices": _weighted_choices(BLUE_SKILL_BOOK_KEYS),
        },
        {
            "chance": 0.01,
            "min_quantity": 1,
            "max_quantity": 1,
            "choices": _weighted_choices(["experience_watermelon"]),
        },
    ]


@pytest.mark.parametrize(
    ("key", "name", "rarity", "cost", "guest_key", "image"),
    [
        (
            "jade_rabbit_summon_scroll",
            "玉兔召唤卷轴",
            "green",
            30,
            "myth_moon_jade_rabbit",
            "mawencai_guest_scroll.png",
        ),
        (
            "wu_gang_summon_scroll",
            "吴刚召唤卷轴",
            "blue",
            60,
            "myth_moon_wu_gang",
            "liangshanbo_guest_scroll.png",
        ),
        (
            "chang_e_summon_scroll",
            "嫦娥召唤卷轴",
            "purple",
            120,
            "hist_xianqin_0413",
            "zhuyingtai_guest_scroll.png",
        ),
    ],
)
def test_mid_autumn_scroll_contract(key, name, rarity, cost, guest_key, image):
    scroll = _items()[key]

    assert scroll["name"] == name
    assert scroll["effect_type"] == "tool"
    assert scroll["rarity"] == rarity
    assert scroll["tradeable"] is True
    assert scroll["is_usable"] is True
    assert scroll["image"] == image
    assert scroll["effect_payload"] == {
        "action": "summon_guest",
        "required_items": {"mid_autumn_mooncake": cost},
        "exclusive_template_keys": [guest_key],
        "choices": [{"template_key": guest_key, "weight": 100}],
    }
