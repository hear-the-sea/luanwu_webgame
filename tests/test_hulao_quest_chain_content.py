from pathlib import Path

import yaml

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
    assert stages[0]["drop_table"]["sishui_battle_report"] == 1
    assert stages[1]["entry_cost"] == {"sishui_battle_report": 1}
    assert stages[1]["drop_table"]["hulao_war_banner"] == 1
    assert stages[2]["entry_cost"] == {"hulao_war_banner": 1}
    assert stages[2]["drop_table"]["baimenlou_fate_seal"] == 1


def test_baimenlou_has_the_requested_five_bosses_and_final_rewards():
    missions = {entry["key"]: entry for entry in _load_yaml("mission_templates.yaml")["missions"]}
    mission = missions["baimenlou_mingyun_jueze"]

    assert mission["name"] == "飞将浮沉录·第三关：白门楼命运抉择"
    assert [entry["label"] for entry in mission["enemy_guests"]] == [
        "吕布",
        "高顺",
        "张辽",
        "陈宫",
        "臧霸",
    ]
    assert {
        "baimenlou_fate_seal",
        "lvbu_guest_scroll",
        "zhaoyun_guest_scroll",
        "diaochan_guest_scroll",
        "equip_panlonggun",
        "book_dragon_roar",
        "book_stratagem_burst",
        "book_prison_break_blade",
    } <= mission["drop_table"].keys()


def test_hulao_replaces_recruitment_scrolls_with_lvbu_equipment():
    missions = {entry["key"]: entry for entry in _load_yaml("mission_templates.yaml")["missions"]}
    drop_table = missions["hulao_qunying_zhan_lvbu"]["drop_table"]

    assert {
        "equip_fangtianhuaji": 0.008,
        "equip_longlinzhanjia": 0.006,
        "equip_chituma": 0.005,
    }.items() <= drop_table.items()
    assert not {"lvbu_guest_scroll", "zhaoyun_guest_scroll", "diaochan_guest_scroll"} & drop_table.keys()


def test_hulao_quest_item_images_are_present():
    items = {entry["key"]: entry for entry in _load_yaml("item_templates.yaml")["items"]}

    for key in ("sishui_battle_report", "hulao_war_banner", "baimenlou_fate_seal"):
        assert (DATA_DIR / "images" / "items" / items[key]["image"]).is_file()
