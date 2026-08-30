"""
门客装备 payload 校验与模板预览。
"""

from __future__ import annotations

from typing import Any

from core.game_data.troop_device_bonus_display import format_raw_troop_device_bonus

from ..models import GearTemplate, GuestRarity
from ..utils.equipment_utils import EQUIP_SLOT_MAP, SET_STAT_FIELD_MAP

GEAR_EXTRA_STAT_FIELDS = {
    "hp": "hp_bonus",
    "force": "force",
    "intellect": "intellect",
    "defense": "defense_stat",
    "agility": "agility",
    "luck": "luck",
    "troop_capacity": "troop_capacity_bonus",
}

GEAR_TEMPLATE_META_FIELDS = {"set_key", "set_description", "set_bonus", "troop_stat_bonus"}


def require_mapping(raw: Any, *, field_name: str) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise AssertionError(f"invalid guest equipment {field_name}: {raw!r}")
    return raw


def require_string(raw: Any, *, field_name: str, allow_blank: bool = False) -> str:
    if not isinstance(raw, str):
        raise AssertionError(f"invalid guest equipment {field_name}: {raw!r}")
    value = raw.strip()
    if not allow_blank and not value:
        raise AssertionError(f"invalid guest equipment {field_name}: {raw!r}")
    return value


def require_int(raw: Any, *, field_name: str, minimum: int | None = None) -> int:
    if isinstance(raw, bool):
        raise AssertionError(f"invalid guest equipment {field_name}: {raw!r}")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid guest equipment {field_name}: {raw!r}") from exc
    if minimum is not None and value < minimum:
        raise AssertionError(f"invalid guest equipment {field_name}: {raw!r}")
    return value


def normalize_extra_stats(raw: Any) -> dict[str, int]:
    payload = require_mapping(raw, field_name="extra_stats")
    normalized: dict[str, int] = {}
    for key, value in payload.items():
        normalized_key = require_string(key, field_name="extra_stats key")
        if normalized_key not in GEAR_EXTRA_STAT_FIELDS:
            raise AssertionError(f"invalid guest equipment extra_stats key: {key!r}")
        normalized[normalized_key] = require_int(value, field_name=f"extra_stats[{normalized_key}]")
    return normalized


def extract_extra_stats_from_item_payload(raw: Any) -> dict[str, int]:
    payload = require_mapping(raw, field_name="item_template.effect_payload")
    extra_stats_payload = {key: value for key, value in payload.items() if key not in GEAR_TEMPLATE_META_FIELDS}
    return normalize_extra_stats(extra_stats_payload)


def normalize_active_set_bonus(raw: Any) -> dict[str, int]:
    payload = require_mapping(raw, field_name="set_bonus")
    normalized: dict[str, int] = {}
    for key, value in payload.items():
        normalized_key = require_string(key, field_name="set_bonus key")
        if normalized_key not in SET_STAT_FIELD_MAP:
            raise AssertionError(f"invalid guest equipment set_bonus key: {key!r}")
        normalized[normalized_key] = require_int(value, field_name=f"set_bonus[{normalized_key}]")
    return normalized


def normalize_template_set_bonus(raw: Any) -> dict[str, Any] | list[dict[str, Any]]:
    if isinstance(raw, list):
        normalized_entries: list[dict[str, Any]] = []
        for entry in raw:
            normalized = normalize_template_set_bonus(entry)
            if isinstance(normalized, list):
                normalized_entries.extend(normalized)
            else:
                normalized_entries.append(normalized)
        return normalized_entries
    payload = require_mapping(raw, field_name="set_bonus")
    pieces = payload.get("pieces")
    bonuses = payload.get("bonus") or payload.get("bonuses") or payload

    if pieces is not None:
        normalized_pieces = require_int(pieces, field_name="set_bonus[pieces]", minimum=1)
    else:
        normalized_pieces = None

    bonus_map = require_mapping(bonuses, field_name="set_bonus bonus")
    normalized_bonus = normalize_active_set_bonus(bonus_map)
    if normalized_pieces is None:
        return normalized_bonus
    return {
        "pieces": normalized_pieces,
        "bonus": normalized_bonus,
    }


def build_gear_template_defaults(item_template: Any, *, slot: str) -> dict[str, Any]:
    payload = require_mapping(getattr(item_template, "effect_payload", None), field_name="item_template.effect_payload")
    extra_stats = extract_extra_stats_from_item_payload(payload)
    return {
        "name": require_string(getattr(item_template, "name", None), field_name="item_template.name"),
        "slot": slot,
        "rarity": getattr(item_template, "rarity", GuestRarity.GRAY),
        "set_key": require_string(payload.get("set_key", ""), field_name="set_key", allow_blank=True),
        "set_description": require_string(
            payload.get("set_description", ""),
            field_name="set_description",
            allow_blank=True,
        ),
        "set_bonus": normalize_template_set_bonus(payload.get("set_bonus")),
        "attack_bonus": 0,
        "defense_bonus": 0,
        "extra_stats": extra_stats,
    }


def build_gear_template_preview(item_template: Any) -> GearTemplate | None:
    effect_type = require_string(getattr(item_template, "effect_type", None), field_name="item_template.effect_type")
    slot = EQUIP_SLOT_MAP.get(effect_type)
    if not slot:
        return None
    preview = GearTemplate(
        key=require_string(getattr(item_template, "key", None), field_name="item_template.key"),
        **build_gear_template_defaults(item_template, slot=slot),
    )
    payload = require_mapping(getattr(item_template, "effect_payload", None), field_name="item_template.effect_payload")
    setattr(
        preview,
        "troop_stat_bonus_summary",
        format_raw_troop_device_bonus(payload.get("troop_stat_bonus")) if slot == "device" else "",
    )
    return preview
