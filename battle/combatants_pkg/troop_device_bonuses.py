"""
Troop device bonus helpers.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from math import isfinite
from typing import Any

from gameplay.models import ItemTemplate
from guests.guest_combat_stats import is_live_guest_model
from guests.models import GearItem

ALLOWED_TROOP_CLASSES = frozenset({"dao", "qiang", "jian", "quan", "gong"})
ALLOWED_STATS = frozenset({"hp", "attack", "defense", "agility"})

StatBonusValue = dict[str, int | float]
TroopStatBonuses = dict[str, dict[str, StatBonusValue]]


def _coerce_positive_number(raw_value: Any) -> int | float:
    if raw_value is None or isinstance(raw_value, bool):
        return 0
    try:
        parsed = float(raw_value)
    except (OverflowError, TypeError, ValueError):
        return 0
    if not isfinite(parsed) or parsed <= 0:
        return 0
    if parsed.is_integer():
        return int(parsed)
    return parsed


def _collect_equipped_template_keys_by_guest(guests: Iterable[Any]) -> dict[int, list[str]]:
    guest_ids = [guest.pk for guest in guests if is_live_guest_model(guest) and getattr(guest, "pk", None)]
    if not guest_ids:
        return {}

    keys_by_guest: dict[int, list[str]] = {int(guest_id): [] for guest_id in guest_ids}
    for guest_id, template_key in GearItem.objects.filter(guest_id__in=guest_ids).values_list(
        "guest_id", "template__key"
    ):
        if template_key:
            keys_by_guest.setdefault(int(guest_id), []).append(template_key)
    return keys_by_guest


def _merge_troop_stat_bonus_payload(bonuses: TroopStatBonuses, raw_troop_bonus: Any) -> None:
    if not isinstance(raw_troop_bonus, Mapping):
        return

    for troop_class, raw_stats in raw_troop_bonus.items():
        if troop_class not in ALLOWED_TROOP_CLASSES or not isinstance(raw_stats, Mapping):
            continue

        for stat in ALLOWED_STATS:
            flat_value = _coerce_positive_number(raw_stats.get(f"{stat}_flat"))
            pct_value = _coerce_positive_number(raw_stats.get(f"{stat}_pct"))
            if flat_value <= 0 and pct_value <= 0:
                continue

            troop_bonus = bonuses.setdefault(troop_class, {})
            stat_bonus = troop_bonus.setdefault(stat, {"flat": 0, "pct": 0.0})
            stat_bonus["flat"] += flat_value
            stat_bonus["pct"] += pct_value


def _merge_normalized_troop_device_bonuses(
    bonuses: TroopStatBonuses,
    source: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> None:
    for troop_class, raw_stats in source.items():
        if troop_class not in ALLOWED_TROOP_CLASSES or not isinstance(raw_stats, Mapping):
            continue
        for stat, raw_bonus in raw_stats.items():
            if stat not in ALLOWED_STATS or not isinstance(raw_bonus, Mapping):
                continue
            flat_value = _coerce_positive_number(raw_bonus.get("flat"))
            pct_value = _coerce_positive_number(raw_bonus.get("pct"))
            if flat_value <= 0 and pct_value <= 0:
                continue
            troop_bonus = bonuses.setdefault(troop_class, {})
            stat_bonus = troop_bonus.setdefault(stat, {"flat": 0, "pct": 0.0})
            stat_bonus["flat"] += flat_value
            stat_bonus["pct"] += pct_value


def _load_device_template_payloads(template_keys: Iterable[str]) -> dict[str, Mapping[str, Any]]:
    unique_keys = {key for key in template_keys if key}
    if not unique_keys:
        return {}

    return {
        key: payload
        for key, payload in ItemTemplate.objects.filter(
            key__in=unique_keys,
            effect_type="equip_device",
        ).values_list("key", "effect_payload")
        if isinstance(payload, Mapping)
    }


def build_troop_device_bonuses_by_guest(guests: Iterable[Any]) -> list[TroopStatBonuses]:
    """Return each guest's own device contribution, preserving input order."""

    guest_list = list(guests)
    keys_by_guest = _collect_equipped_template_keys_by_guest(guest_list)
    template_payloads = _load_device_template_payloads(
        template_key for template_keys in keys_by_guest.values() for template_key in template_keys
    )

    bonuses_by_guest: list[TroopStatBonuses] = []
    for guest in guest_list:
        if is_live_guest_model(guest) and getattr(guest, "pk", None):
            guest_bonuses: TroopStatBonuses = {}
            for template_key in keys_by_guest.get(int(guest.pk), []):
                payload = template_payloads.get(template_key)
                if payload is not None:
                    _merge_troop_stat_bonus_payload(guest_bonuses, payload.get("troop_stat_bonus"))
            bonuses_by_guest.append(guest_bonuses)
            continue

        serialized_bonuses = getattr(guest, "troop_device_bonuses", None)
        guest_bonuses = {}
        if isinstance(serialized_bonuses, Mapping):
            _merge_normalized_troop_device_bonuses(guest_bonuses, serialized_bonuses)
        bonuses_by_guest.append(guest_bonuses)

    return bonuses_by_guest


def build_troop_device_bonuses(guests: Iterable[Any]) -> TroopStatBonuses:
    bonuses: TroopStatBonuses = {}

    for guest_bonuses in build_troop_device_bonuses_by_guest(guests):
        _merge_normalized_troop_device_bonuses(bonuses, guest_bonuses)

    return bonuses


def apply_troop_device_bonus(
    *,
    base_value: int | float,
    troop_class: str,
    stat: str,
    device_bonuses: Mapping[str, Mapping[str, Mapping[str, Any]]] | None,
) -> int | float:
    if not device_bonuses:
        return base_value

    troop_bonus = device_bonuses.get(troop_class)
    if not isinstance(troop_bonus, Mapping):
        return base_value

    stat_bonus = troop_bonus.get(stat)
    if not isinstance(stat_bonus, Mapping):
        return base_value

    flat_value = _coerce_positive_number(stat_bonus.get("flat"))
    pct_value = _coerce_positive_number(stat_bonus.get("pct"))
    return base_value + flat_value + (base_value * pct_value)
