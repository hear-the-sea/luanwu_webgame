from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from core.utils.yaml_loader import ensure_mapping, load_yaml_data

logger = logging.getLogger(__name__)
GUEST_GROWTH_RULES_PATH = Path(__file__).resolve().parent.parent / "data" / "guest_growth_rules.yaml"

RARITY_KEYS = ("black", "gray", "green", "red", "blue", "purple", "orange")
ATTRIBUTE_KEYS = ("force", "intellect", "defense", "agility")
ARCHETYPE_KEYS = ("civil", "military")

DEFAULT_GUEST_GROWTH_RULES: dict[str, Any] = {
    "rarity_hp_profiles": {
        "black": {"base": 100},
        "gray": {"base": 250},
        "green": {"base": 400},
        "red": {"base": 450},
        "blue": {"base": 600},
        "purple": {"base": 800},
        "orange": {"base": 1000},
    },
    "rarity_skill_point_gains": {
        "black": 1,
        "gray": 1,
        "green": 1,
        "red": 1,
        "blue": 1,
        "purple": 1,
        "orange": 1,
    },
    "rarity_attribute_growth_range": {
        "black": [1, 3],
        "gray": [2, 5],
        "green": [3, 7],
        "red": [4, 7],
        "blue": [5, 9],
        "purple": [6, 11],
        "orange": [8, 14],
    },
    "archetype_attribute_weights": {
        "military": {"force": 40, "intellect": 15, "defense": 23, "agility": 22},
        "civil": {"force": 20, "intellect": 40, "defense": 20, "agility": 20},
    },
}


def _to_non_negative_int(raw: Any, default: int) -> int:
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return max(0, int(default))


def _normalize_range(raw: Any, default: tuple[int, int]) -> tuple[int, int]:
    values: list[Any] | tuple[Any, ...]
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        values = raw
    elif isinstance(raw, dict):
        values = [raw.get("min"), raw.get("max")]
    else:
        values = [default[0], default[1]]

    minimum = _to_non_negative_int(values[0], default[0])
    maximum = _to_non_negative_int(values[1], default[1])
    if maximum < minimum:
        maximum = minimum
    return minimum, maximum


def _normalize_hp_profiles(raw: Any) -> dict[str, dict[str, int]]:
    payload = ensure_mapping(raw, logger=logger, context="guest growth rules.rarity_hp_profiles")
    defaults = DEFAULT_GUEST_GROWTH_RULES["rarity_hp_profiles"]
    result: dict[str, dict[str, int]] = {}
    for rarity in RARITY_KEYS:
        item = ensure_mapping(
            payload.get(rarity), logger=logger, context=f"guest growth rules.rarity_hp_profiles.{rarity}"
        )
        default_item = defaults[rarity]
        result[rarity] = {
            "base": _to_non_negative_int(item.get("base"), default_item["base"]),
        }
    return result


def _normalize_skill_point_gains(raw: Any) -> dict[str, int]:
    payload = ensure_mapping(raw, logger=logger, context="guest growth rules.rarity_skill_point_gains")
    defaults = DEFAULT_GUEST_GROWTH_RULES["rarity_skill_point_gains"]
    return {rarity: _to_non_negative_int(payload.get(rarity), defaults[rarity]) for rarity in RARITY_KEYS}


def _normalize_attribute_growth_ranges(raw: Any) -> dict[str, tuple[int, int]]:
    payload = ensure_mapping(raw, logger=logger, context="guest growth rules.rarity_attribute_growth_range")
    defaults = DEFAULT_GUEST_GROWTH_RULES["rarity_attribute_growth_range"]
    return {rarity: _normalize_range(payload.get(rarity), tuple(defaults[rarity])) for rarity in RARITY_KEYS}


def _normalize_attribute_weights(raw: Any) -> dict[str, dict[str, int]]:
    payload = ensure_mapping(raw, logger=logger, context="guest growth rules.archetype_attribute_weights")
    defaults = DEFAULT_GUEST_GROWTH_RULES["archetype_attribute_weights"]
    result: dict[str, dict[str, int]] = {}
    for archetype in ARCHETYPE_KEYS:
        item = ensure_mapping(
            payload.get(archetype),
            logger=logger,
            context=f"guest growth rules.archetype_attribute_weights.{archetype}",
        )
        default_item = defaults[archetype]
        normalized = {attr: _to_non_negative_int(item.get(attr), default_item[attr]) for attr in ATTRIBUTE_KEYS}
        result[archetype] = normalized if sum(normalized.values()) > 0 else dict(default_item)
    return result


def normalize_guest_growth_rules(raw: Any) -> dict[str, Any]:
    root = ensure_mapping(raw, logger=logger, context="guest growth rules root") if raw is not None else {}
    return {
        "rarity_hp_profiles": _normalize_hp_profiles(root.get("rarity_hp_profiles")),
        "rarity_skill_point_gains": _normalize_skill_point_gains(root.get("rarity_skill_point_gains")),
        "rarity_attribute_growth_range": _normalize_attribute_growth_ranges(root.get("rarity_attribute_growth_range")),
        "archetype_attribute_weights": _normalize_attribute_weights(root.get("archetype_attribute_weights")),
    }


@lru_cache(maxsize=1)
def load_guest_growth_rules() -> dict[str, Any]:
    raw = load_yaml_data(
        GUEST_GROWTH_RULES_PATH,
        logger=logger,
        context="guest growth rules config",
        default=DEFAULT_GUEST_GROWTH_RULES,
    )
    return normalize_guest_growth_rules(raw)


RARITY_HP_PROFILES: dict[str, dict[str, int]] = {}
RARITY_SKILL_POINT_GAINS: dict[str, int] = {}
RARITY_ATTRIBUTE_GROWTH_RANGE: dict[str, tuple[int, int]] = {}
MILITARY_ATTRIBUTE_WEIGHTS: dict[str, int] = {}
CIVIL_ATTRIBUTE_WEIGHTS: dict[str, int] = {}


def _refresh_guest_growth_exports() -> None:
    config = load_guest_growth_rules()

    RARITY_HP_PROFILES.clear()
    RARITY_HP_PROFILES.update(config["rarity_hp_profiles"])

    RARITY_SKILL_POINT_GAINS.clear()
    RARITY_SKILL_POINT_GAINS.update(config["rarity_skill_point_gains"])

    RARITY_ATTRIBUTE_GROWTH_RANGE.clear()
    RARITY_ATTRIBUTE_GROWTH_RANGE.update(config["rarity_attribute_growth_range"])

    MILITARY_ATTRIBUTE_WEIGHTS.clear()
    MILITARY_ATTRIBUTE_WEIGHTS.update(config["archetype_attribute_weights"]["military"])

    CIVIL_ATTRIBUTE_WEIGHTS.clear()
    CIVIL_ATTRIBUTE_WEIGHTS.update(config["archetype_attribute_weights"]["civil"])


def clear_guest_growth_rules_cache() -> None:
    load_guest_growth_rules.cache_clear()
    _refresh_guest_growth_exports()


_refresh_guest_growth_exports()
