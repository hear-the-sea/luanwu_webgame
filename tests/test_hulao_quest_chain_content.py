from pathlib import Path

import yaml

from tests.mission_reward_content_helpers import (
    assert_fixed_reward,
    assert_random_group,
    mission_reward_payload,
    reward_item_keys,
)

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _load_yaml(filename: str) -> dict:
    return yaml.safe_load((DATA_DIR / filename).read_text(encoding="utf-8"))


def test_hulao_quest_chain_consumes_the_previous_stage_token():
    missions = {entry["key"]: entry for entry in _load_yaml("mission_templates.yaml")["missions"]}

    stages = [
        missions["sishui_huaxiong_chengwei"],
        missions["hulao_qunying_zhan_lvbu"],
        missions["baimenlou_mingyun_jueze"],
    ]

    assert [stage["display_order"] for stage in stages] == [1001, 1002, 1003]
    assert stages[0].get("entry_cost", {}) == {}
    assert_fixed_reward(mission_reward_payload(stages[0]), "sishui_battle_report", 1)
    assert stages[1]["entry_cost"] == {"sishui_battle_report": 1}
    assert_fixed_reward(mission_reward_payload(stages[1]), "hulao_war_banner", 1)
    assert stages[2]["entry_cost"] == {"hulao_war_banner": 1}
    assert_random_group(mission_reward_payload(stages[2]), 0.01, 1, {"super_guest_disguise_card": 1})


def test_baimenlou_has_the_requested_five_bosses_and_final_rewards():
    missions = {entry["key"]: entry for entry in _load_yaml("mission_templates.yaml")["missions"]}
    mission = missions["baimenlou_mingyun_jueze"]

    assert mission["name"] == "飞将浮沉录：白门楼命运抉择"
    assert [entry["label"] for entry in mission["enemy_guests"]] == [
        "吕布",
        "高顺",
        "张辽",
        "陈宫",
        "臧霸",
    ]
    assert {
        "lvbu_guest_scroll",
        "super_guest_disguise_card",
        "diaochan_guest_scroll",
        "equip_panlonggun",
        "book_dragon_roar",
        "book_stratagem_burst",
        "book_prison_break_blade",
    } <= reward_item_keys(mission_reward_payload(mission))
    payload = mission_reward_payload(mission)
    assert_random_group(payload, 0.1, 1, {"equip_panlonggun": 1})
    assert_random_group(payload, 0.35, 1, {"equip_pojunzhanjia": 1, "equip_tianjiguan": 1})
    assert_fixed_reward(payload, "baimenlou_dragon_fragment", 1)


def test_hulao_replaces_recruitment_scrolls_with_lvbu_equipment():
    missions = {entry["key"]: entry for entry in _load_yaml("mission_templates.yaml")["missions"]}
    payload = mission_reward_payload(missions["hulao_qunying_zhan_lvbu"])
    assert_random_group(
        payload,
        0.1,
        1,
        {"equip_fangtianhuaji": 1, "equip_longlinzhanjia": 1, "equip_chituma": 1},
    )
    assert_fixed_reward(payload, "hulao_halberd_fragment", 1)
    assert not {"lvbu_guest_scroll", "zhaoyun_guest_scroll", "diaochan_guest_scroll"} & reward_item_keys(payload)


def test_hulao_quest_item_images_are_present():
    items = {entry["key"]: entry for entry in _load_yaml("item_templates.yaml")["items"]}

    for key in ("sishui_battle_report", "hulao_war_banner", "baimenlou_fate_seal"):
        assert (DATA_DIR / "images" / "items" / items[key]["image"]).is_file()
