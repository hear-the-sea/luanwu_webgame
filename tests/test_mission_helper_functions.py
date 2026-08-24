from __future__ import annotations

from types import SimpleNamespace

import pytest

from gameplay.views.mission_helpers import (
    build_drop_lists,
    build_mission_data,
    build_selection_summary,
    collect_mission_asset_keys,
    iter_choice_pool_keys,
    order_missions_for_task_board,
    parse_drop_value,
    parse_positive_ids,
)


def test_parse_positive_ids_deduplicates_and_preserves_order():
    assert parse_positive_ids(["3", "1", "3", "2"]) == [3, 1, 2]


def test_parse_positive_ids_rejects_non_positive_values():
    assert parse_positive_ids(["1", "0"]) is None
    assert parse_positive_ids(["1", "oops"]) is None


def test_build_mission_data_applies_extra_attempts():
    missions = [
        SimpleNamespace(key="m1", daily_limit=3),
        SimpleNamespace(key="m2", daily_limit=1),
    ]

    rows = build_mission_data(missions, {"m1": 2, "m2": 1}, {"m1": 1})

    assert rows[0]["daily_limit"] == 4
    assert rows[0]["remaining"] == 2
    assert rows[1]["daily_limit"] == 1
    assert rows[1]["remaining"] == 0


def test_build_selection_summary_handles_missing_selection():
    missions_by_key = {"m1": SimpleNamespace(key="m1", daily_limit=2)}

    selected_mission, selected_attempts, selected_daily_limit, selected_remaining = build_selection_summary(
        None,
        missions_by_key,
        {"m1": 1},
        {"m1": 1},
    )

    assert selected_mission is None
    assert selected_attempts == 0
    assert selected_daily_limit == 0
    assert selected_remaining == 0


def test_order_missions_for_task_board_uses_display_order_then_id():
    missions = [
        SimpleNamespace(key="later_task", id=1, display_order=1000),
        SimpleNamespace(key="huashan_lunjian", id=2, display_order=2),
        SimpleNamespace(key="jingyanggang", id=3, display_order=1),
        SimpleNamespace(key="same_order_earlier_id", id=4, display_order=1000),
    ]

    ordered = order_missions_for_task_board(missions)

    assert [mission.key for mission in ordered] == [
        "jingyanggang",
        "huashan_lunjian",
        "later_task",
        "same_order_earlier_id",
    ]


def test_collect_mission_asset_keys_includes_choice_pool_entries():
    missions = [
        SimpleNamespace(
            enemy_guests=[],
            enemy_troops={},
            drop_table={
                "nichang_random_piece": {
                    "chance": 0.1,
                    "choices": ["equip_nichangyuyi", "equip_nichangwuxie", "equip_nichangjian"],
                }
            },
            probability_drop_table={
                "equip_nichangyuyi": 1,
                "equip_nichangwuxie": 1,
                "equip_nichangjian": 1,
            },
        )
    ]

    _enemy_keys, _troop_keys, drop_keys = collect_mission_asset_keys(missions)

    assert "nichang_random_piece" in drop_keys
    assert "equip_nichangyuyi" in drop_keys
    assert "equip_nichangwuxie" in drop_keys
    assert "equip_nichangjian" in drop_keys


def test_collect_mission_asset_keys_rejects_invalid_enemy_guest_entry():
    missions = [
        SimpleNamespace(
            enemy_guests=[123],
            enemy_troops={},
            drop_table={},
            probability_drop_table={},
        )
    ]

    with pytest.raises(AssertionError, match="invalid mission enemy_guests entry"):
        collect_mission_asset_keys(missions)


def test_collect_mission_asset_keys_rejects_invalid_drop_table_choices():
    missions = [
        SimpleNamespace(
            enemy_guests=[],
            enemy_troops={},
            drop_table={"bad_pool": {"choices": "bad"}},
            probability_drop_table={},
        )
    ]

    with pytest.raises(AssertionError, match="invalid mission drop choices"):
        collect_mission_asset_keys(missions)


def test_collect_mission_asset_keys_rejects_invalid_enemy_troops_key():
    missions = [
        SimpleNamespace(
            enemy_guests=[],
            enemy_troops={"": 1},
            drop_table={},
            probability_drop_table={},
        )
    ]

    with pytest.raises(AssertionError, match="invalid mission enemy_troops key"):
        collect_mission_asset_keys(missions)


def test_collect_mission_asset_keys_rejects_invalid_probability_drop_table_key():
    missions = [
        SimpleNamespace(
            enemy_guests=[],
            enemy_troops={},
            drop_table={},
            probability_drop_table={1: 1},
        )
    ]

    with pytest.raises(AssertionError, match="invalid mission probability_drop_table key"):
        collect_mission_asset_keys(missions)


def test_build_drop_lists_prefers_probability_drop_table_for_choice_pool_display():
    mission = SimpleNamespace(
        drop_table={
            "nichang_random_piece": {
                "chance": 0.1,
                "choices": ["equip_nichangyuyi", "equip_nichangwuxie", "equip_nichangjian"],
            }
        },
        probability_drop_table={
            "equip_nichangyuyi": 1,
            "equip_nichangwuxie": 1,
            "equip_nichangjian": 1,
        },
    )
    item_templates = {
        "equip_nichangyuyi": SimpleNamespace(name="霓裳羽衣"),
        "equip_nichangwuxie": SimpleNamespace(name="霓裳舞鞋"),
        "equip_nichangjian": SimpleNamespace(name="霓裳剑"),
    }
    loot_rarities = {
        "equip_nichangyuyi": "green",
        "equip_nichangwuxie": "green",
        "equip_nichangjian": "green",
    }

    guaranteed_drops, probability_drops = build_drop_lists(
        mission,
        {},
        item_templates,
        {},
        loot_rarities,
    )

    assert guaranteed_drops == []
    assert probability_drops == [
        {
            "key": "equip_nichangyuyi",
            "name": "霓裳羽衣",
            "label": "霓裳羽衣 ×1",
            "count": 1,
            "rarity": "green",
            "image_url": "",
        },
        {
            "key": "equip_nichangwuxie",
            "name": "霓裳舞鞋",
            "label": "霓裳舞鞋 ×1",
            "count": 1,
            "rarity": "green",
            "image_url": "",
        },
        {
            "key": "equip_nichangjian",
            "name": "霓裳剑",
            "label": "霓裳剑 ×1",
            "count": 1,
            "rarity": "green",
            "image_url": "",
        },
    ]


def test_build_drop_lists_expands_choice_pool_preview_items_without_probability_table():
    mission = SimpleNamespace(
        drop_table={
            "blueprint_top_blue": {
                "chance": 0.45,
                "choices": ["blueprint_qinglongkui", "blueprint_qinglongjia"],
            }
        },
        probability_drop_table={},
    )
    item_templates = {
        "blueprint_qinglongkui": SimpleNamespace(name="青龙盔图纸"),
        "blueprint_qinglongjia": SimpleNamespace(name="青龙甲图纸"),
    }

    guaranteed_drops, probability_drops = build_drop_lists(
        mission,
        {},
        item_templates,
        {},
        {"blueprint_qinglongkui": "blue", "blueprint_qinglongjia": "blue"},
    )

    assert guaranteed_drops == []
    assert probability_drops == [
        {
            "key": "blueprint_qinglongkui",
            "name": "青龙盔图纸",
            "label": "青龙盔图纸 ×1",
            "count": 1,
            "rarity": "blue",
            "image_url": "",
        },
        {
            "key": "blueprint_qinglongjia",
            "name": "青龙甲图纸",
            "label": "青龙甲图纸 ×1",
            "count": 1,
            "rarity": "blue",
            "image_url": "",
        },
    ]


def test_build_drop_lists_deduplicates_single_choice_pool_preview_items():
    mission = SimpleNamespace(
        drop_table={
            "kasukabe_crayon": 1,
            "kasukabe_crayon_bonus_1": {
                "chance": 0.55,
                "count": 1,
                "choices": ["kasukabe_crayon"],
            },
            "kasukabe_crayon_bonus_2": {
                "chance": 0.25,
                "count": 2,
                "choices": ["kasukabe_crayon"],
            },
            "kasukabe_crayon_bonus_4": {
                "chance": 0.08,
                "count": 4,
                "choices": ["kasukabe_crayon"],
            },
            "kasukabe_crayon_bonus_10": {
                "chance": 0.015,
                "count": 10,
                "choices": ["kasukabe_crayon"],
            },
        },
        probability_drop_table={},
    )
    item_templates = {"kasukabe_crayon": SimpleNamespace(name="小新的蜡笔")}

    guaranteed_drops, probability_drops = build_drop_lists(
        mission,
        {},
        item_templates,
        {},
        {"kasukabe_crayon": "blue"},
    )

    assert [drop["key"] for drop in guaranteed_drops] == ["kasukabe_crayon"]
    assert probability_drops == []


def test_build_drop_lists_includes_icon_display_data_for_non_silver_drops():
    mission = SimpleNamespace(
        drop_table={"silver": 2000, "test_item": 2},
        probability_drop_table={},
    )
    item_templates = {
        "test_item": SimpleNamespace(
            name="测试物品",
            image=SimpleNamespace(url="/media/items/test.png"),
        )
    }

    guaranteed_drops, probability_drops = build_drop_lists(
        mission,
        {"silver": "银两", "test_item": "测试物品"},
        item_templates,
        {},
        {"test_item": "blue"},
    )

    assert probability_drops == []
    assert guaranteed_drops == [
        {
            "key": "silver",
            "name": "银两",
            "label": "银两 ×2000",
            "count": 2000,
            "rarity": "default",
            "image_url": "",
        },
        {
            "key": "test_item",
            "name": "测试物品",
            "label": "测试物品 ×2",
            "count": 2,
            "rarity": "blue",
            "image_url": "/media/items/test.png",
        },
    ]


def test_build_drop_lists_rejects_invalid_drop_table_container():
    mission = SimpleNamespace(drop_table="bad-drop-table", probability_drop_table={})

    with pytest.raises(AssertionError, match="invalid mission drop_table"):
        build_drop_lists(mission, {}, {}, {}, {})


def test_build_drop_lists_rejects_invalid_probability_drop_table_key():
    mission = SimpleNamespace(drop_table={}, probability_drop_table={1: 1})

    with pytest.raises(AssertionError, match="invalid mission probability_drop_table key"):
        build_drop_lists(mission, {}, {}, {}, {})


def test_parse_drop_value_rejects_invalid_payload():
    with pytest.raises(AssertionError, match="invalid mission drop chance"):
        parse_drop_value({"chance": "bad"})

    with pytest.raises(AssertionError, match="invalid mission drop count"):
        parse_drop_value({"count": "bad"})

    with pytest.raises(AssertionError, match="invalid mission drop value"):
        parse_drop_value("bad")


def test_iter_choice_pool_keys_rejects_invalid_entries():
    with pytest.raises(AssertionError, match="invalid mission drop choices"):
        iter_choice_pool_keys({"choices": "bad"})

    with pytest.raises(AssertionError, match="invalid mission drop choice entry"):
        iter_choice_pool_keys({"choices": ["ok", 123]})

    with pytest.raises(AssertionError, match="invalid mission drop choice entry"):
        iter_choice_pool_keys({"choices": ["ok", {"key": " "}]})
