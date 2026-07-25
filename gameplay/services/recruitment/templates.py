from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from django.conf import settings

from core.utils import safe_non_negative_int, safe_positive_int
from core.utils.yaml_loader import ensure_list, ensure_mapping, load_yaml_data

logger = logging.getLogger(__name__)


def _normalize_recruit_config(raw: Any, *, troop_key: str) -> dict[str, Any] | None:
    if raw is None:
        return None

    recruit = ensure_mapping(raw, logger=logger, context=f"troop_templates[{troop_key}].recruit")
    if not recruit:
        return None

    equipment_raw = ensure_list(recruit.get("equipment"), logger=logger, context=f"{troop_key}.recruit.equipment")
    equipment = [item_key for item in equipment_raw if (item_key := str(item or "").strip())]
    tech_key = str(recruit.get("tech_key") or "").strip() or None

    return {
        "tech_key": tech_key,
        "tech_level": safe_non_negative_int(recruit.get("tech_level"), 0),
        "equipment": equipment,
        "retainer_cost": safe_positive_int(recruit.get("retainer_cost"), 1),
        "base_duration": safe_positive_int(recruit.get("base_duration"), 120),
    }


def _normalize_troop_templates_payload(raw: Any) -> dict[str, Any]:
    data = ensure_mapping(raw, logger=logger, context="troop_templates root")
    troops_raw = ensure_list(data.get("troops"), logger=logger, context="troop_templates.troops")
    normalized_troops: list[dict[str, Any]] = []

    for entry in troops_raw:
        troop = ensure_mapping(entry, logger=logger, context="troop_templates.troops[]")
        if not troop:
            continue

        key = str(troop.get("key") or "").strip()
        if not key:
            logger.warning("Skip troop template without key: %r", troop)
            continue

        normalized = dict(troop)
        normalized["key"] = key
        normalized["name"] = str(troop.get("name") or "未知兵种")
        normalized["description"] = str(troop.get("description") or "")
        normalized["base_attack"] = safe_non_negative_int(troop.get("base_attack"), 0)
        normalized["base_defense"] = safe_non_negative_int(troop.get("base_defense"), 0)
        normalized["base_hp"] = safe_non_negative_int(troop.get("base_hp"), 0)
        normalized["speed_bonus"] = safe_non_negative_int(troop.get("speed_bonus"), 0)
        normalized["avatar"] = str(troop.get("avatar") or "")
        normalized["recruit"] = _normalize_recruit_config(troop.get("recruit"), troop_key=key)
        normalized_troops.append(normalized)

    return {"troops": normalized_troops}


@lru_cache(maxsize=1)
def load_troop_templates(file_path: str | None = None) -> dict[str, Any]:
    path = Path(file_path) if file_path else (settings.BASE_DIR / "data" / "troop_templates.yaml")
    raw = load_yaml_data(path, logger=logger, context="troop templates", default={})
    return _normalize_troop_templates_payload(raw)


@lru_cache(maxsize=1)
def _build_troop_index() -> dict[str, dict[str, Any]]:
    data = load_troop_templates()
    result: dict[str, dict[str, Any]] = {}
    for troop in data.get("troops", []):
        if not isinstance(troop, dict):
            continue
        key = str(troop.get("key") or "").strip()
        if key:
            result[key] = troop
    return result


def clear_troop_cache() -> None:
    load_troop_templates.cache_clear()
    _build_troop_index.cache_clear()


def get_troop_template(troop_key: str) -> dict[str, Any] | None:
    return _build_troop_index().get(troop_key)


def get_recruit_config(troop_key: str) -> dict[str, Any] | None:
    troop = get_troop_template(troop_key)
    if not troop:
        return None
    return troop.get("recruit")
