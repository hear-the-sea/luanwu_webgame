from __future__ import annotations

from pathlib import Path

import yaml

from tests.mission_reward_content_helpers import (
    assert_fixed_reward,
    assert_random_group,
    mission_reward_payload,
    reward_item_keys,
)

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
JUNIOR_ORDER = [
    "jingyanggang",
    "huashan_lunjian",
    "fugui_shanzhuang",
    "wulongshan",
    "wagangzhai",
    "biwu_zhaoqin",
    "jiguanshou_chuxian",
    "taozi_fenban",
]
EQUIPMENT_POOLS = {
    "jingyanggang": ("jingyang_equipment_pool", 0.50, ["equip_hupixue", "equip_huwenkui"]),
    "huashan_lunjian": (
        "huashan_equipment_pool",
        0.60,
        ["equip_shushengzhesan", "equip_yanlingmao"],
    ),
    "fugui_shanzhuang": (
        "fugui_equipment_pool",
        0.80,
        ["equip_yupei", "equip_fudai", "equip_zhujian", "equip_moyuyan"],
    ),
    "wulongshan": (
        "wulong_equipment_pool",
        0.70,
        ["equip_baoutoukui", "equip_tiegugun", "equip_youxiaxue", "equip_mengguma"],
    ),
    "wagangzhai": (
        "wagang_equipment_pool",
        0.75,
        [
            "equip_suozijia",
            "equip_shanwenjia",
            "equip_tiexianjia",
            "equip_pofengji",
            "equip_tongshanchui",
            "equip_tiehufu",
            "equip_yiqiyaopai",
            "equip_xiliangma",
            "equip_chibiao",
        ],
    ),
    "biwu_zhaoqin": (
        "biwu_equipment_pool",
        0.80,
        ["equip_xuantiejia", "equip_qingtongsuanchou", "equip_tongjing", "equip_qingzongma"],
    ),
    "jiguanshou_chuxian": (
        "jiguanshou_equipment_pool",
        0.80,
        {
            "blueprint_jixiemao": 1,
            "equip_qingtongsuanchou": 3,
            "blueprint_xiaoxingjiguanshu": 3,
            "blueprint_tongchanjiguan": 3,
        },
    ),
    "taozi_fenban": (
        "taozi_equipment_pool",
        0.85,
        [
            "equip_qingzhujia",
            "equip_qingtengguan",
            "equip_zhuyelu",
            "equip_tayunxue",
            "equip_jifengxue",
            "equip_hushenfu",
            "equip_taomupai",
            "equip_tanmunianzhu",
            "equip_taomujian",
            "equip_luopan",
            "equip_taxuema",
        ],
    ),
}


def _missions() -> dict[str, dict]:
    payload = yaml.safe_load((DATA_DIR / "mission_templates.yaml").read_text(encoding="utf-8"))
    return {row["key"]: row for row in payload["missions"]}


def test_junior_missions_follow_the_validated_progression_order_and_entry_difficulty():
    missions = _missions()

    assert [
        key
        for key, _mission in sorted(
            ((key, missions[key]) for key in JUNIOR_ORDER),
            key=lambda row: row[1]["display_order"],
        )
    ] == JUNIOR_ORDER
    assert missions["jingyanggang"]["enemy_technology"]["guest_level"] == 15
    assert missions["huashan_lunjian"]["enemy_technology"] == {
        "level": 2,
        "guest_level": 12,
        "guest_bonus": 0,
    }
    assert [row["key"] for row in missions["huashan_lunjian"]["enemy_guests"]] == [
        "task_huashan_jianwang",
        "task_huashan_jianjing",
        "task_huashan_jianjing",
        "task_huashan_audience_a",
    ]


def test_junior_mission_rewards_use_one_equipment_pick_per_pool():
    missions = _missions()

    for mission_key, (_pool_key, chance, choices) in EQUIPMENT_POOLS.items():
        payload = mission_reward_payload(missions[mission_key])
        assert_random_group(payload, chance, 1, choices if isinstance(choices, dict) else dict.fromkeys(choices, 1))


def test_junior_mission_fixed_rewards_and_attempt_limits_match_balance_budget():
    missions = _missions()
    expected = {
        "jingyanggang": (7000, 3, 5),
        "huashan_lunjian": (8000, 3, 5),
        "fugui_shanzhuang": (14000, 3, 5),
        "wulongshan": (15000, 3, 5),
        "wagangzhai": (20000, 3, 5),
        "biwu_zhaoqin": (20000, 3, 5),
        "jiguanshou_chuxian": (24000, 2, 1),
        "taozi_fenban": (0, 1, 0),
    }

    for mission_key, (silver, daily_limit, card_limit) in expected.items():
        mission = missions[mission_key]
        assert mission["drop_table"].get("silver", 0) == silver
        assert mission["daily_limit"] == daily_limit
        assert mission.get("mission_card_daily_limit", 5) == card_limit


def test_peaches_are_concentrated_in_the_daily_peach_mission_and_biwu_blueprint_is_five_percent():
    missions = _missions()

    for mission_key in JUNIOR_ORDER[:-1]:
        assert "experience_peach" not in reward_item_keys(mission_reward_payload(missions[mission_key]))
    assert_fixed_reward(mission_reward_payload(missions["taozi_fenban"]), "experience_peach", 15)
    assert_random_group(mission_reward_payload(missions["biwu_zhaoqin"]), 0.05, 1, {"blueprint_jiguanxiong": 1})


def test_jiguan_chuniao_has_a_separate_target_blueprint_without_duplicate_extra_pool_entries():
    payload = mission_reward_payload(_missions()["jiguan_chuniao"])
    main_key = "blueprint_jiguanchuniao"
    assert_random_group(payload, 0.4, 1, {main_key: 1})
    extra_keys = {"blueprint_jixiemao", "blueprint_kuileimuren", "blueprint_qingji"}
    assert_random_group(payload, 0.2, 1, dict.fromkeys(extra_keys, 1))
    assert main_key not in {row["item_key"] for row in payload["item_rewards"]}
    assert (
        sum(choice["item_key"] == main_key for group in payload["random_item_groups"] for choice in group["choices"])
        == 1
    )
    recipes = yaml.safe_load((DATA_DIR / "forge_blueprints.yaml").read_text(encoding="utf-8"))["recipes"]
    recipe_costs = {row["blueprint_key"]: row["costs"] for row in recipes}
    assert set(payload["blueprint_material_support"]) == extra_keys | {main_key}
    for key, rewards in payload["blueprint_material_support"].items():
        assert {row["item_key"]: row["min_quantity"] for row in rewards} == {
            material: cost // 2 for material, cost in recipe_costs[key].items() if cost // 2 > 0
        }
        assert all(row["min_quantity"] == row["max_quantity"] for row in rewards)
