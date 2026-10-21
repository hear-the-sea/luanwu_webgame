from importlib import import_module
from pathlib import Path

import yaml

DATA = Path(__file__).resolve().parents[1] / "data"
progression_rules = import_module("gameplay.migrations.0240_zhanlong_progressive_difficulty")
MISSION_KEYS = (
    "zhanlong_yanshan_shizi",
    "zhanlong_changban_tuwei",
    "zhanlong_changban_duanqiao",
    "zhanlong_wuhu_shizhen",
)
TOKEN_KEYS = (
    "zhanlong_white_horse_token",
    "zhanlong_changban_guard_token",
    "zhanlong_dangyang_valor_letter",
)


def _catalog(filename, section):
    payload = yaml.safe_load((DATA / filename).read_text(encoding="utf-8"))
    return {entry["key"]: entry for entry in payload[section]}


def _guests():
    payload = yaml.safe_load((DATA / "guests/zhanlong.yaml").read_text(encoding="utf-8"))
    return {entry["key"]: entry for entries in payload["heroes"].values() for entry in entries}


def test_zhanlong_chain_guarantees_each_next_entry_and_allows_five_cards_per_mission():
    missions = _catalog("mission_templates.yaml", "missions")
    items = _catalog("item_templates.yaml", "items")
    chain = [missions[key] for key in MISSION_KEYS]
    assert not chain[0].get("entry_cost")
    assert [mission["display_order"] for mission in chain] == sorted(mission["display_order"] for mission in chain)
    for index, mission in enumerate(chain):
        assert mission["name"].startswith(f"战龙在野第{'一二三四'[index]}关")
        assert mission["daily_limit"] == 1
        assert mission["mission_card_daily_limit"] == 5
        assert mission["battle_type"] == "task1"
        chest_key = "mission_chest_v3_" + mission["key"]
        assert mission["drop_table"] == {"silver": [90000, 110000, 130000, 180000][index], chest_key: 1}
        payload = items[chest_key]["effect_payload"]
        assert payload["mission_key"] == mission["key"]
        if index < len(TOKEN_KEYS):
            token_key = TOKEN_KEYS[index]
            assert {"item_key": token_key, "min_quantity": 1, "max_quantity": 1} in payload["item_rewards"]
            assert chain[index + 1]["entry_cost"] == {token_key: 1}
            assert items[token_key]["tradeable"] is False
            assert items[token_key]["is_usable"] is False


def test_zhanlong_dust_army_is_display_only_with_real_bridge_guards():
    missions = _catalog("mission_templates.yaml", "missions")
    bridge = missions[MISSION_KEYS[2]]
    assert bridge["enemy_guests"] == [{"key": "task_zhanlong_zhangfei_bridge", "label": "张飞"}]
    assert bridge["enemy_troops_display"] == {"bloodthirsty_cavalry": 1000000}
    assert bridge["enemy_troops"] == progression_rules.MISSION_UPDATES[bridge["key"]]["enemy_troops"]
    assert all("enemy_troops_display" not in missions[key] for key in MISSION_KEYS if key != bridge["key"])
    assert "烟尘" in bridge["description"] and "考验" in bridge["description"]
    assert "五虎如今已齐聚" in missions[MISSION_KEYS[3]]["description"]


def test_zhanlong_bosses_have_independent_stats_skills_and_existing_portraits():
    missions = _catalog("mission_templates.yaml", "missions")
    skills = _catalog("guest_skills.yaml", "skills")
    guests = _guests()
    expected_skills = {
        "task_zhanlong_tiebeixiong": {"draw_enemy_blades", "zhanlong_iron_hammer"},
        "task_zhanlong_caochun": {"zhanlong_cavalry_assault", "zhanlong_cavalry_command"},
        "task_zhanlong_zhangfei_bridge": {"zhanlong_bridge_tiger_dragon_roar"},
        "task_zhanlong_guanyu": {"zhanlong_azure_dragon_slash"},
        "task_zhanlong_zhangfei": {"zhanlong_tiger_dragon_roar"},
        "task_zhanlong_zhaoyun": {"zhanlong_dragon_assault"},
        "task_zhanlong_machao": {"zhanlong_xiliang_charge"},
        "task_zhanlong_huangzhong": {"zhanlong_hundred_pace_shot"},
    }
    assert set(guests) == set(expected_skills)
    used_keys = {enemy["key"] for key in MISSION_KEYS for enemy in missions[key]["enemy_guests"]}
    assert used_keys == set(guests)
    for key, guest in guests.items():
        assert guest["recruitable"] is False
        assert set(guest["skills"]) == expected_skills[key]
        assert set(guest["skills"]) <= skills.keys()
        assert guest["custom_stats"]["base_hp"] > 0
        assert (DATA / "images/guests" / guest["avatar"]).is_file()
    finale = missions[MISSION_KEYS[3]]["enemy_guests"]
    assert [guest["label"] for guest in finale] == ["关羽", "张飞", "赵云", "马超", "黄忠"]
    assert guests["task_zhanlong_machao"]["custom_stats"]["agility"] == max(
        guests[guest["key"]]["custom_stats"]["agility"] for guest in finale
    )
    assert guests["task_zhanlong_zhangfei_bridge"]["custom_stats"]["force"] > max(
        guests[guest["key"]]["custom_stats"]["force"] for guest in finale
    )


def test_zhanlong_chests_resolve_rewards_and_keep_scroll_roll_independent():
    items = _catalog("item_templates.yaml", "items")
    for key in MISSION_KEYS:
        chest = items["mission_chest_v3_" + key]
        assert chest["effect_type"] == "loot_box" and chest["is_usable"] is True
        assert chest["tradeable"] is False
        assert (DATA / "images/items" / chest["image"]).is_file()
        payload = chest["effect_payload"]
        assert payload["mission_reward_version"] == 1
        assert payload["reward_balance_version"] == 3
        assert payload["experience_fruit_source"] == "battle_salvage"
        for reward in payload["item_rewards"]:
            assert reward["item_key"] in items
            assert reward["min_quantity"] == reward["max_quantity"] > 0
        for group in payload["random_item_groups"]:
            assert 0 < group["chance"] <= 1
            assert all(choice["item_key"] in items for choice in group["choices"])
    second = items["mission_chest_v3_" + MISSION_KEYS[1]]["effect_payload"]
    mount_group = next(group for group in second["random_item_groups"] if group["label"] == "equip_zhaoyeyushizi")
    assert mount_group["chance"] == 0.1
    assert mount_group["choices"] == [{"item_key": "equip_zhaoyeyushizi", "weight": 1}]
    final = items["mission_chest_v3_" + MISSION_KEYS[3]]["effect_payload"]
    scroll_group = next(group for group in final["random_item_groups"] if group["label"] == "zhaoyun_guest_scroll")
    assert scroll_group["chance"] == 0.01
    assert scroll_group["min_quantity"] == scroll_group["max_quantity"] == 1
    assert scroll_group["choices"] == [{"item_key": "zhaoyun_guest_scroll", "weight": 1}]
    assert all(reward["item_key"] != "zhaoyun_guest_scroll" for reward in final["item_rewards"])
