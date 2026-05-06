from __future__ import annotations

import copy
from typing import Any, Dict, List, cast

from core.utils import safe_float as _safe_float
from core.utils import safe_int as _safe_int


def _coerce_int(value: Any, default: int) -> int:
    result = _safe_int(value, default=default)
    return default if result is None else result


def _coerce_float(value: Any, default: float) -> float:
    result = _safe_float(value, default=default)
    return default if result is None else result


DEFAULT_FORGE_DECOMPOSE_CONFIG: Dict[str, Any] = {
    "supported_rarities": ["green", "blue", "purple", "orange"],
    "rarity_labels": {
        "green": "绿色",
        "blue": "蓝色",
        "purple": "紫色",
        "orange": "橙色",
    },
    "rarity_order": {
        "orange": 4,
        "purple": 3,
        "blue": 2,
        "green": 1,
    },
    "base_materials": {
        "green": {"tong": [2, 5], "xi": [1, 3], "tie": [1, 2]},
        "blue": {"tong": [4, 8], "xi": [2, 5], "tie": [1, 3]},
        "purple": {"tong": [6, 12], "xi": [4, 8], "tie": [2, 5]},
        "orange": {"tong": [8, 16], "xi": [5, 10], "tie": [3, 7]},
    },
    "chance_rewards": {
        "green": {"wood_essence": 0.75, "copper_essence": 0.25},
        "blue": {
            "wood_essence": 0.75,
            "copper_essence": 0.75,
            "xuan_tie_essence": 0.20,
            "air_stone": 0.12,
            "fire_stone": 0.12,
            "earth_stone": 0.12,
            "water_stone": 0.12,
        },
        "purple": {
            "wood_essence": 0.88,
            "copper_essence": 0.88,
            "xuan_tie_essence": 0.35,
            "air_stone": 0.22,
            "fire_stone": 0.22,
            "earth_stone": 0.22,
            "water_stone": 0.22,
        },
        "orange": {
            "wood_essence": 0.95,
            "copper_essence": 0.95,
            "xuan_tie_essence": 0.50,
            "air_stone": 0.35,
            "fire_stone": 0.35,
            "earth_stone": 0.35,
            "water_stone": 0.35,
        },
    },
}

DEFAULT_FORGE_EQUIPMENT_CONFIG: Dict[str, Dict[str, Any]] = {}

DEFAULT_FORGE_BLUEPRINT_CONFIG: Dict[str, Any] = {"recipes": []}


def _require_non_empty_string(raw: Any, *, field_name: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise AssertionError(f"invalid forge config {field_name}: {raw!r}")
    return raw.strip()


def _require_positive_int(raw: Any, *, field_name: str, minimum: int = 1) -> int:
    if raw is None or isinstance(raw, bool):
        raise AssertionError(f"invalid forge config {field_name}: {raw!r}")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid forge config {field_name}: {raw!r}") from exc
    if value < minimum:
        raise AssertionError(f"invalid forge config {field_name}: {raw!r}")
    return value


def _require_probability(raw: Any, *, field_name: str) -> float:
    if raw is None or isinstance(raw, bool):
        raise AssertionError(f"invalid forge config {field_name}: {raw!r}")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid forge config {field_name}: {raw!r}") from exc
    if value < 0 or value > 1:
        raise AssertionError(f"invalid forge config {field_name}: {raw!r}")
    return value


def _normalize_quantity_range(raw_value: Any, fallback: list[int]) -> list[int]:
    if not isinstance(raw_value, (list, tuple)) or len(raw_value) != 2:
        return list(fallback)
    minimum = _coerce_int(raw_value[0], default=fallback[0])
    maximum = _coerce_int(raw_value[1], default=fallback[1])
    minimum = max(0, minimum)
    maximum = max(minimum, maximum)
    return [minimum, maximum]


def _normalize_decompose_config(raw: Any) -> Dict[str, Any]:
    config: Dict[str, Any] = copy.deepcopy(DEFAULT_FORGE_DECOMPOSE_CONFIG)
    if not isinstance(raw, dict):
        return config

    supported_rarities = raw.get("supported_rarities")
    if isinstance(supported_rarities, list):
        normalized_supported = [str(rarity).strip() for rarity in supported_rarities if str(rarity).strip()]
        if normalized_supported:
            config["supported_rarities"] = normalized_supported

    rarity_labels = raw.get("rarity_labels")
    if isinstance(rarity_labels, dict):
        for rarity, label in rarity_labels.items():
            rarity_key = str(rarity).strip()
            label_value = str(label).strip()
            if rarity_key and label_value:
                config["rarity_labels"][rarity_key] = label_value

    rarity_order = raw.get("rarity_order")
    if isinstance(rarity_order, dict):
        for rarity, order in rarity_order.items():
            rarity_key = str(rarity).strip()
            if not rarity_key:
                continue
            config["rarity_order"][rarity_key] = max(
                0, _coerce_int(order, default=config["rarity_order"].get(rarity_key, 0))
            )

    base_materials = raw.get("base_materials")
    if isinstance(base_materials, dict):
        for rarity, materials in base_materials.items():
            rarity_key = str(rarity).strip()
            if not rarity_key or not isinstance(materials, dict):
                continue
            fallback_map = cast(Dict[str, list[int]], config["base_materials"].get(rarity_key, {}))
            normalized_map: Dict[str, list[int]] = dict(fallback_map)
            for mat_key, raw_range in materials.items():
                mat_name = str(mat_key).strip()
                if not mat_name:
                    continue
                fallback_range = normalized_map.get(mat_name, [1, 1])
                normalized_map[mat_name] = _normalize_quantity_range(raw_range, fallback_range)
            if normalized_map:
                config["base_materials"][rarity_key] = normalized_map

    chance_rewards = raw.get("chance_rewards")
    if isinstance(chance_rewards, dict):
        for rarity, rewards in chance_rewards.items():
            rarity_key = str(rarity).strip()
            if not rarity_key or not isinstance(rewards, dict):
                continue
            chance_fallback_map = cast(Dict[str, float], config["chance_rewards"].get(rarity_key, {}))
            chance_map: Dict[str, float] = dict(chance_fallback_map)
            for reward_key, raw_prob in rewards.items():
                reward_name = str(reward_key).strip()
                if not reward_name:
                    continue
                fallback_prob = float(chance_map.get(reward_name, 0.0))
                prob = _coerce_float(raw_prob, default=fallback_prob)
                chance_map[reward_name] = max(0.0, min(1.0, prob))
            if chance_map:
                config["chance_rewards"][rarity_key] = chance_map

    available_rarities = [
        rarity
        for rarity in config["supported_rarities"]
        if rarity in config["base_materials"] and rarity in config["chance_rewards"]
    ]
    if available_rarities:
        config["supported_rarities"] = available_rarities
    else:
        config["supported_rarities"] = list(DEFAULT_FORGE_DECOMPOSE_CONFIG["supported_rarities"])
    return config


def _normalize_equipment_config(raw: Any) -> Dict[str, Dict[str, Any]]:
    root = raw
    if isinstance(raw, dict) and "equipment" in raw:
        root = raw.get("equipment")
    if not isinstance(root, dict):
        raise AssertionError(f"invalid forge config equipment root: {raw!r}")

    config: Dict[str, Dict[str, Any]] = {}
    for raw_key, raw_item in root.items():
        item_key = _require_non_empty_string(raw_key, field_name="equipment item key")
        if not isinstance(raw_item, dict):
            raise AssertionError(f"invalid forge config equipment item payload: {raw_item!r}")

        category = _require_non_empty_string(raw_item.get("category"), field_name=f"equipment.{item_key}.category")

        materials_raw = raw_item.get("materials")
        if not isinstance(materials_raw, dict):
            raise AssertionError(f"invalid forge config equipment.{item_key}.materials: {materials_raw!r}")
        materials: Dict[str, int] = {}
        for mat_key, mat_amount in materials_raw.items():
            normalized_mat_key = _require_non_empty_string(
                mat_key,
                field_name=f"equipment.{item_key}.materials key",
            )
            materials[normalized_mat_key] = _require_positive_int(
                mat_amount,
                field_name=f"equipment.{item_key}.materials.{normalized_mat_key}",
            )

        config[item_key] = {
            "category": category,
            "materials": materials,
            "base_duration": _require_positive_int(
                raw_item.get("base_duration"),
                field_name=f"equipment.{item_key}.base_duration",
            ),
            "required_forging": _require_positive_int(
                raw_item.get("required_forging"),
                field_name=f"equipment.{item_key}.required_forging",
            ),
        }
    return config


def _normalize_blueprint_recipe(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise AssertionError(f"invalid forge config blueprint recipe: {raw!r}")

    blueprint_key = _require_non_empty_string(raw.get("blueprint_key"), field_name="blueprint.blueprint_key")
    result_item_key = _require_non_empty_string(raw.get("result_item_key"), field_name="blueprint.result_item_key")

    required_forging = _require_positive_int(raw.get("required_forging"), field_name="blueprint.required_forging")
    quantity_out = _require_positive_int(raw.get("quantity_out"), field_name="blueprint.quantity_out")

    costs_raw = raw.get("costs")
    if not isinstance(costs_raw, dict):
        raise AssertionError(f"invalid forge config blueprint.costs: {costs_raw!r}")
    costs: Dict[str, int] = {}
    for key, value in costs_raw.items():
        item_key = _require_non_empty_string(key, field_name="blueprint.costs key")
        costs[item_key] = _require_positive_int(value, field_name=f"blueprint.costs.{item_key}")

    return {
        "blueprint_key": blueprint_key,
        "result_item_key": result_item_key,
        "required_forging": required_forging,
        "quantity_out": quantity_out,
        "costs": costs,
        "description": str(raw.get("description") or "").strip(),
    }


def _normalize_blueprint_config(raw: Any) -> Dict[str, Any]:
    config: Dict[str, Any] = copy.deepcopy(DEFAULT_FORGE_BLUEPRINT_CONFIG)
    if not isinstance(raw, dict):
        raise AssertionError(f"invalid forge config blueprint root: {raw!r}")

    recipes_raw = raw.get("recipes")
    if not isinstance(recipes_raw, list):
        raise AssertionError(f"invalid forge config blueprint recipes: {recipes_raw!r}")

    recipes: List[Dict[str, Any]] = []
    for entry in recipes_raw:
        recipes.append(_normalize_blueprint_recipe(entry))
    config["recipes"] = recipes
    return config
