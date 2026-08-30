from __future__ import annotations

from pathlib import Path

import yaml

ITEM_TEMPLATES_PATH = Path(__file__).resolve().parent.parent / "data" / "item_templates.yaml"
FORGE_EQUIPMENT_PATH = Path(__file__).resolve().parent.parent / "data" / "forge_equipment.yaml"
FORGE_BLUEPRINTS_PATH = Path(__file__).resolve().parent.parent / "data" / "forge_blueprints.yaml"
FORGE_MATERIAL_KEYS = {
    "zitanmu",
    "heiyuanshi",
    "jingangmei",
    "tiemu",
    "shuiqumu",
    "paozi",
    "zaozi",
    "gaolu",
}
ELEMENT_STONE_KEYS = {"air_stone", "earth_stone", "fire_stone", "water_stone"}
MIN_FORGE_MATERIAL_TOTAL = {
    ("green", "device"): 14,
    ("blue", "set"): 13,
    ("blue", "device"): 21,
    ("purple", "set"): 24,
    ("purple", "device"): 49,
    ("orange", "device"): 90,
}
MIN_ELEMENT_STONE_TOTAL = {"green": 1, "blue": 1, "purple": 2, "orange": 5}
SUPPORTED_EQUIPMENT_STATS = {"hp", "force", "intellect", "defense", "agility", "luck", "troop_capacity"}
RARITY_ORDER = ("black", "green", "blue", "purple", "orange")
MIN_RARITY_SCORE_RATIO = 1.10
SLOT_CAPACITY = {
    "helmet": 1,
    "armor": 1,
    "shoes": 1,
    "weapon": 1,
    "mount": 1,
    "ornament": 3,
    "device": 3,
}
MAX_DIRECT_TROOP_CAPACITY = 520
MAX_DIRECT_LUCK = 210
# The 2026-08 equipment rebalance raised the catalog maxima to 393 agility and
# 21,293 effective HP; keep rounded ceilings with a small regression margin.
MAX_DIRECT_AGILITY = 400
MAX_DIRECT_EFFECTIVE_HP = 22000
MAX_ORANGE_NON_HP_ATTRIBUTE_SUM = 260
ALL_DEVICE_TROOP_CLASSES = ("dao", "qiang", "jian", "quan", "gong", "scout")
DEVICE_TROOP_BONUS_DESIGN = {
    "equip_taotieding": {
        troop_class: {"attack_pct": 0.0025, "defense_pct": 0.0025, "hp_pct": 0.0025}
        for troop_class in ALL_DEVICE_TROOP_CLASSES
    },
    "equip_xiaoxingjiguanshu": {troop_class: {"hp_pct": 0.005} for troop_class in ALL_DEVICE_TROOP_CLASSES},
    "equip_tongchanjiguan": {troop_class: {"attack_pct": 0.005} for troop_class in ALL_DEVICE_TROOP_CLASSES},
    "equip_jixiemao": {"gong": {"hp_pct": 0.01}},
    "equip_jiguanchuniao": {"gong": {"attack_pct": 0.01}},
    "equip_kuileimuren": {"qiang": {"hp_pct": 0.01}},
    "equip_qingji": {"jian": {"attack_pct": 0.01}},
    "equip_jiguanxiong": {"quan": {"attack_pct": 0.01}},
    "equip_muniuliuma": {troop_class: {"hp_pct": 0.005} for troop_class in ALL_DEVICE_TROOP_CLASSES},
    "equip_kuileiren": {"jian": {"attack_pct": 0.005, "defense_pct": 0.005, "hp_pct": 0.005}},
    "equip_xuanwujigui": {"qiang": {"attack_pct": 0.005, "defense_pct": 0.005, "hp_pct": 0.005}},
    "equip_feiyuan": {troop_class: {"attack_pct": 0.005} for troop_class in ALL_DEVICE_TROOP_CLASSES},
    "equip_chixiaojifeng": {"gong": {"attack_pct": 0.015}},
    "equip_mojiajiguanren": {
        troop_class: {"attack_pct": 0.005, "defense_pct": 0.005, "hp_pct": 0.005}
        for troop_class in ALL_DEVICE_TROOP_CLASSES
    },
}

EQUIPMENT_STORAGE_BY_RARITY = {"black": 50, "green": 75, "blue": 100, "purple": 150, "orange": 200}
MOUNT_STORAGE_BY_RARITY = {"black": 75, "green": 100, "blue": 125, "purple": 175, "orange": 250}
CONSUMABLE_STORAGE_BY_RARITY = {"black": 2, "green": 5, "blue": 10, "purple": 20, "orange": 40}
TOOL_STORAGE_BY_RARITY = {"black": 5, "green": 10, "blue": 25, "purple": 50, "orange": 100}
BLUEPRINT_STORAGE_BY_RARITY = {"black": 25, "green": 50, "blue": 75, "purple": 125, "orange": 200}
GUEST_ITEM_STORAGE_BY_RARITY = {"black": 50, "green": 75, "blue": 100, "purple": 150, "orange": 200}
LOOT_BOX_STORAGE_BY_RARITY = {"black": 10, "green": 25, "blue": 50, "purple": 100, "orange": 150}
RESOURCE_STORAGE = {
    "grain": 1,
    "tong": 2,
    "xi": 3,
    "tie": 5,
    "gold_bar": 250,
    "red_ruby": 150,
    "chunqiu_coin": 150,
    "haorenka": 100,
    "wood_essence": 5,
    "copper_essence": 5,
    "iron_essence": 10,
    "xuan_tie_essence": 10,
    "fire_stone": 10,
    "water_stone": 10,
    "earth_stone": 10,
    "air_stone": 10,
    "zitanmu": 5,
    "heiyuanshi": 5,
    "jingangmei": 5,
    "tiemu": 5,
    "shuiqumu": 2,
    "paozi": 2,
    "zaozi": 2,
    "gaolu": 20,
    "ji": 5,
    "ya": 5,
    "e": 10,
    "zhu": 15,
    "niu": 20,
    "wanyin_flag_fragment": 10,
    "yuxu_broken_seal": 20,
    "sishui_battle_report": 10,
    "hulao_war_banner": 15,
    "baimenlou_fate_seal": 20,
}
RESOURCE_PACK_STORAGE = {
    "resource_pack_silver": 25,
    "resource_pack_grain": 25,
    "resource_pack_mixed": 50,
    "resource_pack_advanced": 100,
}
STRATEGIC_ITEM_STORAGE = {
    "peace_shield_small": 25,
    "peace_shield_medium": 50,
    "peace_shield_large": 100,
    "xisuidan": 150,
    "soul_container": 200,
}


def _load_item_templates() -> dict[str, dict]:
    payload = yaml.safe_load(ITEM_TEMPLATES_PATH.read_text(encoding="utf-8"))
    return {row["key"]: row for row in payload["items"]}


def _load_forge_equipment() -> dict[str, dict]:
    payload = yaml.safe_load(FORGE_EQUIPMENT_PATH.read_text(encoding="utf-8"))
    return payload["equipment"]


def _load_forge_blueprints() -> list[dict]:
    payload = yaml.safe_load(FORGE_BLUEPRINTS_PATH.read_text(encoding="utf-8"))
    return payload["recipes"]


def _expected_storage_space(key: str, item: dict) -> int:
    if key in RESOURCE_STORAGE:
        return RESOURCE_STORAGE[key]
    if key in RESOURCE_PACK_STORAGE:
        return RESOURCE_PACK_STORAGE[key]
    if key in STRATEGIC_ITEM_STORAGE:
        return STRATEGIC_ITEM_STORAGE[key]

    rarity = str(item["rarity"])
    effect_type = str(item["effect_type"])
    if effect_type == "equip_mount":
        return MOUNT_STORAGE_BY_RARITY[rarity]
    if effect_type.startswith("equip_"):
        return EQUIPMENT_STORAGE_BY_RARITY[rarity]
    if effect_type in {"medicine", "experience_items"}:
        return CONSUMABLE_STORAGE_BY_RARITY[rarity]
    if effect_type == "loot_box":
        return LOOT_BOX_STORAGE_BY_RARITY[rarity]
    if effect_type == "skill_book" or key.startswith("blueprint_"):
        return BLUEPRINT_STORAGE_BY_RARITY[rarity]
    if key.endswith(("_guest_card", "_guest_scroll")):
        return GUEST_ITEM_STORAGE_BY_RARITY[rarity]
    if effect_type == "tool":
        return TOOL_STORAGE_BY_RARITY[rarity]
    raise AssertionError(f"未定义容量规则: {key} ({effect_type})")


def _iter_loot_box_item_keys(item: dict) -> list[tuple[str, str]]:
    payload = item.get("effect_payload") or {}
    refs: list[tuple[str, str]] = []
    for field in ("gear_keys", "skill_book_keys"):
        for key in payload.get(field) or []:
            refs.append((field, str(key)))
    for field in ("gear_choices", "skill_book_choices"):
        for choice in payload.get(field) or []:
            if isinstance(choice, dict) and choice.get("item_key"):
                refs.append((field, str(choice["item_key"])))
    for reward in payload.get("item_rewards") or []:
        if isinstance(reward, dict) and reward.get("item_key"):
            refs.append(("item_rewards", str(reward["item_key"])))
    for group in payload.get("random_item_groups") or []:
        if not isinstance(group, dict):
            continue
        for choice in group.get("choices") or []:
            if isinstance(choice, dict) and choice.get("item_key"):
                refs.append(("random_item_groups", str(choice["item_key"])))
    return refs


def _equipment_score(effect_payload: dict) -> float:
    return (
        float(effect_payload.get("hp", 0)) / 54.0
        + float(effect_payload.get("defense", 0))
        + float(effect_payload.get("force", 0))
        + float(effect_payload.get("intellect", 0))
        + float(effect_payload.get("agility", 0))
        + float(effect_payload.get("luck", 0)) * 1.2
        + float(effect_payload.get("troop_capacity", 0)) / 2.0
    )


def _iter_equipment_items(items: dict[str, dict]):
    for key, item in items.items():
        effect_type = str(item.get("effect_type") or "")
        if effect_type.startswith("equip_"):
            yield key, item


def test_device_troop_bonuses_match_approved_design_and_never_grant_agility():
    items = _load_item_templates()
    device_keys = {key for key, item in items.items() if item.get("effect_type") == "equip_device"}

    actual = {key: items[key]["effect_payload"].get("troop_stat_bonus") for key in DEVICE_TROOP_BONUS_DESIGN}

    assert device_keys == set(DEVICE_TROOP_BONUS_DESIGN)
    assert actual == DEVICE_TROOP_BONUS_DESIGN
    for bonus_by_class in actual.values():
        assert bonus_by_class is not None
        for stat_bonus in bonus_by_class.values():
            affected_stats = {key.removesuffix("_pct").removesuffix("_flat") for key in stat_bonus}
            assert affected_stats in ({"attack"}, {"defense"}, {"hp"}, {"attack", "defense", "hp"})
            assert "agility" not in affected_stats


def _top_slot_total(items: dict[str, dict], stat: str) -> int:
    total = 0
    for slot, capacity in SLOT_CAPACITY.items():
        slot_values = []
        for _key, item in _iter_equipment_items(items):
            effect_type = str(item.get("effect_type") or "")
            if effect_type != f"equip_{slot}":
                continue
            payload = item.get("effect_payload") or {}
            slot_values.append(int(payload.get(stat, 0) or 0))
        total += sum(sorted(slot_values, reverse=True)[:capacity])
    return total


def _top_effective_hp_total(items: dict[str, dict]) -> int:
    total = 0
    for slot, capacity in SLOT_CAPACITY.items():
        slot_values = []
        for _key, item in _iter_equipment_items(items):
            effect_type = str(item.get("effect_type") or "")
            if effect_type != f"equip_{slot}":
                continue
            payload = item.get("effect_payload") or {}
            hp = int(payload.get("hp", 0) or 0)
            defense = int(payload.get("defense", 0) or 0)
            slot_values.append(hp + defense * 50)
        total += sum(sorted(slot_values, reverse=True)[:capacity])
    return total


def _non_hp_attribute_sum(effect_payload: dict) -> int:
    return sum(int(value) for stat, value in effect_payload.items() if stat != "hp" and isinstance(value, (int, float)))


def test_equipment_payload_uses_supported_stats_only():
    items = _load_item_templates()

    invalid_stats_by_item: dict[str, list[str]] = {}
    for key, item in _iter_equipment_items(items):
        payload = item.get("effect_payload") or {}
        invalid_stats = [
            stat
            for stat, value in payload.items()
            if isinstance(value, (int, float)) and stat not in SUPPORTED_EQUIPMENT_STATS
        ]
        if invalid_stats:
            invalid_stats_by_item[key] = sorted(invalid_stats)

    assert invalid_stats_by_item == {}


def test_loot_box_item_references_exist():
    items = _load_item_templates()
    known_keys = set(items)

    missing_refs: dict[str, list[str]] = {}
    for key, item in items.items():
        if item.get("effect_type") != "loot_box":
            continue
        missing = [ref_key for _field, ref_key in _iter_loot_box_item_keys(item) if ref_key not in known_keys]
        if missing:
            missing_refs[key] = sorted(set(missing))

    assert missing_refs == {}


def test_work_chests_include_tool_item_reward_ranges():
    items = _load_item_templates()
    expected = {
        "work_chest_small": {
            "fangdajing": (0, 2),
            "mission_card": (0, 1),
        },
        "work_chest_medium": {
            "fangdajing": (1, 2),
            "mission_card": (0, 2),
        },
        "work_chest_large": {
            "fangdajing": (1, 3),
            "mission_card": (1, 3),
        },
    }

    actual = {}
    for chest_key in expected:
        rewards = items[chest_key]["effect_payload"].get("item_rewards") or []
        actual[chest_key] = {
            str(reward.get("item_key")): (
                int(reward.get("min_quantity")),
                int(reward.get("max_quantity")),
            )
            for reward in rewards
            if isinstance(reward, dict)
        }

    assert actual == expected


def test_work_chests_include_forge_material_and_currency_random_groups():
    items = _load_item_templates()
    expected = {
        "work_chest_small": [
            (0.80, 1, 2, {"shuiqumu": 5, "paozi": 3, "zaozi": 3}),
            (0.20, 1, 1, {"tiemu": 4, "zitanmu": 3, "heiyuanshi": 3, "jingangmei": 2}),
        ],
        "work_chest_medium": [
            (1.00, 1, 2, {"shuiqumu": 5, "paozi": 3, "zaozi": 3}),
            (0.70, 1, 2, {"tiemu": 4, "zitanmu": 3, "heiyuanshi": 3, "jingangmei": 2}),
            (0.03, 1, 1, {"gaolu": 1}),
        ],
        "work_chest_large": [
            (1.00, 2, 3, {"shuiqumu": 5, "paozi": 3, "zaozi": 3}),
            (1.00, 1, 2, {"tiemu": 4, "zitanmu": 3, "heiyuanshi": 3, "jingangmei": 2}),
            (0.12, 1, 1, {"gaolu": 1}),
            (0.10, 1, 1, {"chunqiu_coin": 1}),
            (0.01, 10, 10, {"chunqiu_coin": 1}),
        ],
    }

    actual = {}
    for chest_key in expected:
        groups = items[chest_key]["effect_payload"].get("random_item_groups") or []
        actual[chest_key] = [
            (
                float(group["chance"]),
                int(group["min_quantity"]),
                int(group["max_quantity"]),
                {str(choice["item_key"]): int(choice["weight"]) for choice in group["choices"]},
            )
            for group in groups
        ]

    assert actual == expected


def test_work_chests_include_starter_equipment_rewards():
    items = _load_item_templates()
    expected_gear_keys = {
        "equip_caomao",
        "equip_suoyi",
        "equip_caoxie",
        "equip_tiejian",
        "equip_cubujia",
        "equip_zhubiankui",
        "equip_mabuxue",
        "equip_qingcongma",
        "equip_xiaomaolv",
    }

    for chest_key in ("work_chest_small", "work_chest_medium", "work_chest_large"):
        actual_gear_keys = set(items[chest_key]["effect_payload"].get("gear_keys") or [])
        assert expected_gear_keys <= actual_gear_keys, chest_key


def test_blueprint_material_costs_enforce_collection_gates():
    items = _load_item_templates()
    recipes = _load_forge_blueprints()
    assert len(recipes) == 36

    orange_gaolu_costs = {
        "blueprint_feiyuan": 7,
        "blueprint_chixiaojifeng": 8,
        "blueprint_mojiajiguanren": 10,
    }
    for recipe in recipes:
        blueprint_key = str(recipe["blueprint_key"])
        result_item = items[str(recipe["result_item_key"])]
        rarity = str(result_item["rarity"])
        kind = "device" if result_item["effect_type"] == "equip_device" else "set"
        costs = recipe["costs"]
        material_costs = {key: int(costs[key]) for key in FORGE_MATERIAL_KEYS if key in costs}
        stone_costs = {key: int(costs[key]) for key in ELEMENT_STONE_KEYS if key in costs}

        assert len(material_costs) >= 2, blueprint_key
        assert sum(material_costs.values()) >= MIN_FORGE_MATERIAL_TOTAL[(rarity, kind)], blueprint_key
        assert sum(stone_costs.values()) >= MIN_ELEMENT_STONE_TOTAL[rarity], blueprint_key
        assert "chunqiu_coin" not in costs, blueprint_key

        if rarity in {"green", "blue"}:
            assert "gaolu" not in costs, blueprint_key
        elif rarity == "purple":
            assert int(costs.get("gaolu", 0)) >= 1, blueprint_key
        else:
            assert int(costs["gaolu"]) == orange_gaolu_costs[blueprint_key], blueprint_key

    representative_costs = {
        "blueprint_xiaoweikaijia": {
            "tiemu": 5,
            "heiyuanshi": 7,
            "jingangmei": 5,
            "zaozi": 5,
            "earth_stone": 2,
        },
        "blueprint_huxianpao": {
            "zitanmu": 9,
            "shuiqumu": 10,
            "tiemu": 3,
            "jingangmei": 3,
            "paozi": 6,
            "zaozi": 2,
            "gaolu": 2,
            "water_stone": 2,
        },
        "blueprint_taotieding": {
            "tiemu": 10,
            "heiyuanshi": 15,
            "jingangmei": 12,
            "paozi": 2,
            "zaozi": 10,
            "gaolu": 4,
            "water_stone": 1,
            "earth_stone": 2,
            "fire_stone": 2,
        },
        "blueprint_mojiajiguanren": {
            "zitanmu": 12,
            "shuiqumu": 8,
            "tiemu": 22,
            "heiyuanshi": 24,
            "jingangmei": 22,
            "paozi": 10,
            "zaozi": 16,
            "gaolu": 10,
            "earth_stone": 2,
            "fire_stone": 2,
            "water_stone": 2,
        },
    }
    recipes_by_key = {str(recipe["blueprint_key"]): recipe for recipe in recipes}
    balanced_cost_keys = FORGE_MATERIAL_KEYS | ELEMENT_STONE_KEYS
    for blueprint_key, expected_costs in representative_costs.items():
        actual_costs = {
            key: int(value)
            for key, value in recipes_by_key[blueprint_key]["costs"].items()
            if key in balanced_cost_keys
        }
        assert actual_costs == expected_costs, blueprint_key


def test_forgeable_equipment_progresses_with_recipe_tier():
    items = _load_item_templates()
    forge_equipment = _load_forge_equipment()
    forgeable_lines = {
        "helmet": ["equip_bumao", "equip_niupimao", "equip_tieyekui", "equip_yulindin", "equip_baihongkui"],
        "armor": ["equip_bupao", "equip_shengpijia", "equip_housipao", "equip_shapijia"],
        "shoes": ["equip_buxie", "equip_yangpixue", "equip_gangpianxue", "equip_yanyuxue"],
        "sword": ["equip_duanjian", "equip_changjian", "equip_qingmangjian", "equip_duanmajian"],
        "dao": ["equip_duandao", "equip_dakandao", "equip_tongchangdao", "equip_jingtiedao"],
        "spear": ["equip_changqiang", "equip_baoweiqiang", "equip_hutoumao", "equip_pansheqiang"],
        "bow": ["equip_changgong", "equip_fanqugong", "equip_tietaigong", "equip_shenbigong"],
        "whip": [
            "equip_changbian",
            "equip_niupibian",
            "equip_jicibian",
            "equip_jiulonggangbian",
            "equip_mingshejiebian",
        ],
    }

    for line_name, keys in forgeable_lines.items():
        required_forging_levels = [int(forge_equipment[key]["required_forging"]) for key in keys]
        assert required_forging_levels == sorted(required_forging_levels), line_name

        scores = [_equipment_score(items[key]["effect_payload"]) for key in keys]
        assert scores == sorted(scores), f"{line_name}: {scores}"
        assert scores[-1] > scores[0], f"{line_name}: {scores}"


def test_forgeable_weapon_lines_have_distinct_secondary_roles():
    items = _load_item_templates()

    sword = items["equip_duanmajian"]["effect_payload"]
    dao = items["equip_jingtiedao"]["effect_payload"]
    spear = items["equip_pansheqiang"]["effect_payload"]
    bow = items["equip_shenbigong"]["effect_payload"]
    whip = items["equip_mingshejiebian"]["effect_payload"]

    assert sword.get("agility", 0) > 0
    assert sword.get("hp", 0) == 0
    assert sword.get("troop_capacity", 0) == 0

    assert dao.get("hp", 0) > 0
    assert dao.get("troop_capacity", 0) == 0

    assert spear.get("troop_capacity", 0) > 0
    assert spear.get("agility", 0) > 0

    assert bow.get("agility", 0) > 0
    assert bow.get("force", 0) > 0

    assert whip.get("luck", 0) > 0
    assert whip.get("agility", 0) > 0


def test_equipment_sets_use_consistent_bonus_definition_per_set():
    items = _load_item_templates()

    def _normalize_set_bonus(raw):
        if isinstance(raw, list):
            return [
                {"pieces": entry.get("pieces"), "bonus": entry.get("bonus")} for entry in raw if isinstance(entry, dict)
            ]
        if isinstance(raw, dict):
            return {"pieces": raw.get("pieces"), "bonus": raw.get("bonus")}
        return {"pieces": None, "bonus": None}

    set_bonus_by_key: dict[str, dict] = {}
    inconsistent_sets: dict[str, list[str]] = {}
    for key, item in _iter_equipment_items(items):
        payload = item.get("effect_payload") or {}
        set_key = str(payload.get("set_key") or "")
        if not set_key:
            continue

        normalized = _normalize_set_bonus(payload.get("set_bonus"))
        current = set_bonus_by_key.get(set_key)
        if current is None:
            set_bonus_by_key[set_key] = normalized
            continue
        if current != normalized:
            inconsistent_sets.setdefault(set_key, []).append(key)

    assert inconsistent_sets == {}


def test_equipment_rarity_progression_has_clear_slot_gaps():
    items = _load_item_templates()

    scores_by_slot_and_rarity: dict[str, dict[str, list[float]]] = {}
    for _key, item in _iter_equipment_items(items):
        effect_type = str(item.get("effect_type") or "")
        slot = effect_type.removeprefix("equip_")
        rarity = str(item.get("rarity") or "")
        slot_scores = scores_by_slot_and_rarity.setdefault(slot, {})
        slot_scores.setdefault(rarity, []).append(_equipment_score(item.get("effect_payload") or {}))

    for slot, rarity_map in scores_by_slot_and_rarity.items():
        previous_average: float | None = None
        previous_rarity: str | None = None
        for rarity in RARITY_ORDER:
            scores = rarity_map.get(rarity)
            if not scores:
                continue

            average_score = sum(scores) / len(scores)
            if previous_average is not None and previous_rarity is not None:
                ratio = average_score / previous_average
                assert average_score > previous_average, f"{slot}: {previous_rarity} -> {rarity}"
                assert ratio >= MIN_RARITY_SCORE_RATIO, f"{slot}: {previous_rarity} -> {rarity} = {ratio:.3f}"
            previous_average = average_score
            previous_rarity = rarity


def test_multi_slot_direct_stat_caps_remain_bounded():
    items = _load_item_templates()

    assert _top_slot_total(items, "troop_capacity") <= MAX_DIRECT_TROOP_CAPACITY
    assert _top_slot_total(items, "luck") <= MAX_DIRECT_LUCK
    assert _top_slot_total(items, "agility") <= MAX_DIRECT_AGILITY
    assert _top_effective_hp_total(items) <= MAX_DIRECT_EFFECTIVE_HP


def test_orange_equipment_non_hp_attribute_sum_stays_within_target_band():
    items = _load_item_templates()

    over_budget = {}
    for key, item in _iter_equipment_items(items):
        if item.get("rarity") != "orange":
            continue
        payload = item.get("effect_payload") or {}
        total = _non_hp_attribute_sum(payload)
        if total > MAX_ORANGE_NON_HP_ATTRIBUTE_SUM:
            over_budget[key] = total

    assert over_budget == {}


def test_all_item_templates_define_explicit_positive_storage_space():
    items = _load_item_templates()

    missing = sorted(key for key, item in items.items() if "storage_space" not in item)
    invalid = {
        key: item.get("storage_space")
        for key, item in items.items()
        if "storage_space" in item
        and (
            not isinstance(item["storage_space"], int)
            or isinstance(item["storage_space"], bool)
            or item["storage_space"] < 1
        )
    }

    assert missing == []
    assert invalid == {}


def test_item_template_storage_space_matches_treasury_balance_matrix():
    items = _load_item_templates()

    mismatches = {
        key: {"actual": item.get("storage_space"), "expected": _expected_storage_space(key, item)}
        for key, item in items.items()
        if item.get("storage_space") != _expected_storage_space(key, item)
    }

    assert mismatches == {}


def test_tuwujian_is_orange_anti_sorcery_sword():
    items = _load_item_templates()

    tuwujian = items["equip_tuwujian"]

    assert tuwujian["name"] == "屠巫剑"
    assert tuwujian["effect_type"] == "equip_weapon"
    assert tuwujian["rarity"] == "orange"
    assert tuwujian["tradeable"] is True
    assert tuwujian["price"] == 52000
    assert tuwujian["storage_space"] == 200
    assert tuwujian["effect_payload"] == {
        "hp": 514,
        "defense": 3,
        "force": 40,
        "intellect": 22,
        "agility": 15,
        "luck": 3,
    }
