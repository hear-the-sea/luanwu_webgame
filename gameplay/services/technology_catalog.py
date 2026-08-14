from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Callable

from django.conf import settings

from core.utils.yaml_loader import ensure_mapping, load_yaml_data

logger = logging.getLogger(__name__)
TECHNOLOGY_TEMPLATES_PATH = settings.BASE_DIR / "data" / "technology_templates.yaml"


def _apply_upgrade_profiles(data: dict[str, Any]) -> dict[str, Any]:
    """将分类级升级预算默认值展开到具体技术，保留技术自身字段优先级。"""
    profiles = data.get("upgrade_profiles")
    technologies = data.get("technologies")
    if not isinstance(profiles, dict) or not isinstance(technologies, list):
        return data

    resolved = dict(data)
    resolved_technologies: list[Any] = []
    for technology in technologies:
        if not isinstance(technology, dict):
            resolved_technologies.append(technology)
            continue
        profile = profiles.get(technology.get("category"), {})
        merged = dict(profile) if isinstance(profile, dict) else {}
        merged.update(technology)
        resolved_technologies.append(merged)
    resolved["technologies"] = resolved_technologies
    return resolved


@lru_cache(maxsize=4)
def load_technology_templates(
    load_yaml_data_func: Callable[..., Any] = load_yaml_data,
) -> dict[str, Any]:
    raw = load_yaml_data_func(
        TECHNOLOGY_TEMPLATES_PATH,
        logger=logger,
        context="technology templates",
        default={},
    )
    return _apply_upgrade_profiles(ensure_mapping(raw, logger=logger, context="technology templates root"))


@lru_cache(maxsize=4)
def build_technology_index(
    load_technology_templates_func: Callable[[], dict[str, Any]] = load_technology_templates,
) -> dict[str, dict[str, Any]]:
    data = load_technology_templates_func()
    result: dict[str, dict[str, Any]] = {}
    for tech in data.get("technologies", []) or []:
        if not isinstance(tech, dict):
            continue
        tech_key = str(tech.get("key") or "").strip()
        if not tech_key:
            continue
        result[tech_key] = tech
    return result


@lru_cache(maxsize=4)
def build_troop_to_class_index(
    load_technology_templates_func: Callable[[], dict[str, Any]] = load_technology_templates,
) -> dict[str, str]:
    data = load_technology_templates_func()
    index: dict[str, str] = {}
    for class_key, class_info in (data.get("troop_classes", {}) or {}).items():
        if not isinstance(class_info, dict):
            continue
        for troop_key in class_info.get("troops", []) or []:
            troop_key_str = str(troop_key).strip()
            if troop_key_str:
                index[troop_key_str] = str(class_key)
    return index


def clear_technology_cache() -> None:
    load_technology_templates.cache_clear()
    build_technology_index.cache_clear()
    build_troop_to_class_index.cache_clear()
