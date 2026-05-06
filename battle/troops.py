from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

from django.conf import settings
from django.core.cache import cache

from core.utils import safe_non_negative_int
from core.utils.yaml_loader import ensure_list, ensure_mapping, load_yaml_data

DEFAULT_TROOP_FILE = Path(settings.BASE_DIR) / "data" / "troop_templates.yaml"
logger = logging.getLogger(__name__)

# 兵种模板缓存键和过期时间
TROOP_TEMPLATES_CACHE_KEY = "troop_templates:db"
TROOP_TEMPLATES_CACHE_TIMEOUT = 300  # 5分钟


def _normalize_troop_template_item(raw: Any) -> tuple[str, dict] | None:
    item = ensure_mapping(raw, logger=logger, context="troop_templates.troops[]")
    if not item:
        return None
    key = str(item.get("key") or "").strip()
    if not key:
        logger.warning("Skip troop template without key: %r", item)
        return None

    return (
        key,
        {
            "label": str(item.get("name") or key),
            "description": str(item.get("description") or ""),
            "base_attack": safe_non_negative_int(item.get("base_attack"), 30),
            "base_defense": safe_non_negative_int(item.get("base_defense"), 20),
            "base_hp": safe_non_negative_int(item.get("base_hp"), 80),
            "speed_bonus": safe_non_negative_int(item.get("speed_bonus"), 10),
            "priority": safe_non_negative_int(item.get("priority"), 0),
            "default_count": safe_non_negative_int(item.get("default_count"), 120),
            "avatar": None,
        },
    )


def load_troop_templates_from_db() -> Dict[str, dict]:
    """
    从数据库加载兵种模板（使用缓存）

    Returns:
        兵种模板字典 {key: data}
    """
    # 尝试从缓存获取
    templates = cache.get(TROOP_TEMPLATES_CACHE_KEY)
    if templates is not None:
        return templates

    from battle.models import TroopTemplate

    templates = {}
    for troop in TroopTemplate.objects.all():
        templates[troop.key] = {
            "label": troop.name,
            "description": troop.description,
            "base_attack": troop.base_attack,
            "base_defense": troop.base_defense,
            "base_hp": troop.base_hp,
            "speed_bonus": troop.speed_bonus,
            "priority": troop.priority,
            "default_count": troop.default_count,
            "avatar": troop.avatar.url if troop.avatar else None,
        }

    # 只有数据库有数据时才缓存
    if templates:
        cache.set(TROOP_TEMPLATES_CACHE_KEY, templates, timeout=TROOP_TEMPLATES_CACHE_TIMEOUT)

    return templates


def invalidate_troop_templates_cache() -> None:
    """
    清除兵种模板缓存（在 TroopTemplate 变更或 YAML 更新时调用）
    """
    cache.delete(TROOP_TEMPLATES_CACHE_KEY)
    load_troop_templates_from_yaml.cache_clear()


@lru_cache()
def load_troop_templates_from_yaml(file_path: str | None = None) -> Dict[str, dict]:
    """
    从 YAML 文件加载兵种模板（回退方案）

    Args:
        file_path: YAML文件路径

    Returns:
        兵种模板字典 {key: data}
    """
    path = Path(file_path or DEFAULT_TROOP_FILE)
    raw = load_yaml_data(path, logger=logger, context="troop templates (yaml fallback)", default={})
    data = ensure_mapping(raw, logger=logger, context="troop_templates root")
    troops = ensure_list(data.get("troops"), logger=logger, context="troop_templates.troops")
    templates = {}
    for raw_item in troops:
        normalized = _normalize_troop_template_item(raw_item)
        if normalized is None:
            continue
        key, payload = normalized
        templates[key] = payload
    return templates


def load_troop_templates(file_path: str | None = None) -> Dict[str, dict]:
    """
    加载兵种模板（优先从数据库，否则从YAML）

    Args:
        file_path: YAML文件路径（回退方案使用）

    Returns:
        兵种模板字典 {key: data}
    """
    # 优先从数据库加载
    templates = load_troop_templates_from_db()

    # 如果数据库为空，从YAML加载
    if not templates:
        templates = load_troop_templates_from_yaml(file_path)

    return templates


def troop_template_list() -> List[dict]:
    templates = load_troop_templates()
    return [{"key": key, **value} for key, value in sorted(templates.items(), key=lambda item: item[1]["priority"])]


def default_troop_loadout() -> Dict[str, int]:
    templates = load_troop_templates()
    loadout = {}
    for key, data in templates.items():
        loadout[key] = safe_non_negative_int(data.get("default_count"), 120)
    return loadout
