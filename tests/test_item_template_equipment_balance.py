from __future__ import annotations

from pathlib import Path

import yaml

ITEM_TEMPLATES_PATH = Path(__file__).resolve().parent.parent / "data" / "item_templates.yaml"
FORGE_EQUIPMENT_PATH = Path(__file__).resolve().parent.parent / "data" / "forge_equipment.yaml"
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
MAX_DIRECT_TROOP_CAPACITY = 220
MAX_DIRECT_LUCK = 210
MAX_DIRECT_AGILITY = 330
MAX_DIRECT_EFFECTIVE_HP = 15000
MAX_ORANGE_NON_HP_ATTRIBUTE_SUM = 100


def _load_item_templates() -> dict[str, dict]:
    payload = yaml.safe_load(ITEM_TEMPLATES_PATH.read_text(encoding="utf-8"))
    return {row["key"]: row for row in payload["items"]}


def _load_forge_equipment() -> dict[str, dict]:
    payload = yaml.safe_load(FORGE_EQUIPMENT_PATH.read_text(encoding="utf-8"))
    return payload["equipment"]


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
    return refs


def _equipment_score(effect_payload: dict) -> float:
    return (
        float(effect_payload.get("hp", 0)) / 18.0
        + float(effect_payload.get("defense", 0)) * 3.0
        + float(effect_payload.get("force", 0))
        + float(effect_payload.get("intellect", 0))
        + float(effect_payload.get("agility", 0)) * 0.8
        + float(effect_payload.get("luck", 0)) * 0.6
        + float(effect_payload.get("troop_capacity", 0)) * 0.25
    )


def _iter_equipment_items(items: dict[str, dict]):
    for key, item in items.items():
        effect_type = str(item.get("effect_type") or "")
        if effect_type.startswith("equip_"):
            yield key, item


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
        assert len(set(scores)) == len(scores), f"{line_name}: {scores}"


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
    assert bow.get("intellect", 0) > 0

    assert whip.get("luck", 0) > 0
    assert whip.get("agility", 0) > 0


def test_equipment_sets_use_consistent_bonus_definition_per_set():
    items = _load_item_templates()

    set_bonus_by_key: dict[str, dict] = {}
    inconsistent_sets: dict[str, list[str]] = {}
    for key, item in _iter_equipment_items(items):
        payload = item.get("effect_payload") or {}
        set_key = str(payload.get("set_key") or "")
        if not set_key:
            continue

        normalized = {
            "pieces": payload.get("set_bonus", {}).get("pieces"),
            "bonus": payload.get("set_bonus", {}).get("bonus"),
        }
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
