from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Dict, Optional

from django.conf import settings

from core.utils import safe_int as _safe_int_core
from core.utils.yaml_loader import ensure_mapping, load_yaml_data


def _coerce_int(value: Any, default: int = 0) -> int:
    """Safe integer coercion, returning int (never None)."""
    result = _safe_int_core(value, default=default)
    return default if result is None else result


logger = logging.getLogger(__name__)
TECHNOLOGY_TEMPLATES_PATH = settings.BASE_DIR / "data" / "technology_templates.yaml"


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@lru_cache(maxsize=1)
def load_technology_templates() -> Dict[str, Any]:
    """
    Load technology templates from YAML.

    Kept in core to allow multiple apps (battle/gameplay/...) to share the same
    read-only calculations without creating cross-app import cycles.
    """
    raw = load_yaml_data(
        TECHNOLOGY_TEMPLATES_PATH,
        logger=logger,
        context="technology templates",
        default={},
    )
    return ensure_mapping(raw, logger=logger, context="technology templates root")


@lru_cache(maxsize=1)
def _build_troop_to_class_index() -> Dict[str, str]:
    data = load_technology_templates()
    index: Dict[str, str] = {}
    for class_key, class_info in (data.get("troop_classes") or {}).items():
        if not isinstance(class_info, dict):
            continue
        for troop_key in class_info.get("troops", []) or []:
            troop_key_str = str(troop_key).strip()
            if troop_key_str:
                index[troop_key_str] = str(class_key)
    return index


def get_troop_class_for_key(troop_key: str) -> Optional[str]:
    """Return troop class key for a troop key (e.g. dao_ke -> dao)."""
    return _build_troop_to_class_index().get(troop_key)


def get_tech_bonus_from_levels(levels: Dict[str, int], effect_type: str, troop_class: Optional[str] = None) -> float:
    """
    Pure data technology bonus calculation (used for AI/enemy/preview).

    Returns a multiplier (e.g. 0.3 means +30%).
    """
    data = load_technology_templates()
    total = 0.0
    for tech in data.get("technologies", []) or []:
        if not isinstance(tech, dict):
            continue
        if tech.get("effect_type") != effect_type:
            continue
        tech_key = str(tech.get("key") or "").strip()
        if not tech_key:
            continue
        tech_troop_class = tech.get("troop_class")
        if tech_troop_class and troop_class and tech_troop_class != troop_class:
            continue
        level = _coerce_int(levels.get(tech_key, 0), 0)
        if level <= 0:
            continue
        effect_per_level = _coerce_float(tech.get("effect_per_level", 0.10), 0.10)
        total += level * effect_per_level
    return total


def get_troop_stat_bonuses_from_levels(levels: Dict[str, int], troop_key: str) -> Dict[str, float]:
    """Return per-stat bonus multipliers for a troop key based on provided tech levels."""
    troop_class = get_troop_class_for_key(troop_key)
    if not troop_class:
        return {}

    bonuses: Dict[str, float] = {}
    for effect_type, stat_key in [
        ("troop_attack", "attack"),
        ("troop_defense", "defense"),
        ("troop_agility", "agility"),
        ("troop_hp", "hp"),
    ]:
        bonus = get_tech_bonus_from_levels(levels, effect_type, troop_class)
        if bonus > 0:
            bonuses[stat_key] = bonus
    return bonuses


def build_uniform_tech_levels(level: int) -> Dict[str, int]:
    """Map a single level to all technology keys, clamped by each tech's max_level."""
    data = load_technology_templates()
    base_level = max(0, _coerce_int(level, 0))
    resolved: Dict[str, int] = {}
    for tech in data.get("technologies", []) or []:
        if not isinstance(tech, dict):
            continue
        tech_key = str(tech.get("key") or "").strip()
        if not tech_key:
            continue
        max_level = max(0, _coerce_int(tech.get("max_level", base_level), base_level))
        resolved[tech_key] = max(0, min(base_level, max_level))
    return resolved


def resolve_enemy_tech_levels(config: Dict[str, Any]) -> Dict[str, int]:
    """
    Merge rules:
    1) config.level sets a uniform baseline; 2) config.levels overrides per key.
    """
    if not config or not isinstance(config, dict):
        return {}
    levels: Dict[str, int] = {}
    if config.get("level") is not None:
        levels = build_uniform_tech_levels(_coerce_int(config.get("level", 0), 0))
    for key, val in (config.get("levels") or {}).items():
        normalized_key = str(key).strip()
        if not normalized_key:
            continue
        levels[normalized_key] = max(0, _coerce_int(val, 0))
    return levels


def get_guest_stat_bonuses(config: Dict[str, Any]) -> Dict[str, float]:
    """
    Compute guest stat bonuses from config.

    Currently supports:
    - guest_bonus: uniform percent bonus (e.g. 0.2 means +20%), agility is half.
    """
    if not config:
        return {}

    bonuses: Dict[str, float] = {}
    if "guest_bonus" in config:
        bonus_percent = _coerce_float(config.get("guest_bonus", 0), 0.0)
        bonuses["attack"] = bonus_percent
        bonuses["defense"] = bonus_percent
        bonuses["hp"] = bonus_percent
        bonuses["agility"] = bonus_percent * 0.5
    return bonuses


def clear_technology_cache() -> None:
    """Clear YAML caches (useful for tests or runtime reload)."""
    load_cache_clear = getattr(load_technology_templates, "cache_clear", None)
    if callable(load_cache_clear):
        load_cache_clear()

    index_cache_clear = getattr(_build_troop_to_class_index, "cache_clear", None)
    if callable(index_cache_clear):
        index_cache_clear()
