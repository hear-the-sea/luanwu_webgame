"""
Troop device bonus helpers.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from math import isfinite
from typing import Any

from gameplay.models import ItemTemplate
from guests.guest_combat_stats import is_live_guest_model
from guests.models import GearItem

ALLOWED_TROOP_CLASSES = frozenset({"dao", "qiang", "jian", "quan", "gong", "scout"})
ALLOWED_STATS = frozenset({"hp", "attack", "defense"})
MAX_EFFECTIVE_COPIES_PER_TEMPLATE = 5

StatBonusValue = dict[str, int | float]
TroopStatBonuses = dict[str, dict[str, StatBonusValue]]
TroopDeviceBonusSource = dict[str, Any]


@dataclass(frozen=True)
class TroopDeviceBonusSummary:
    bonuses: TroopStatBonuses
    devices: list[dict[str, Any]]


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


def _add_bonus_numbers(current: int | float, increment: int | float) -> int | float:
    total = Decimal(str(current)) + Decimal(str(increment))
    if total == total.to_integral_value():
        return int(total)
    return float(total)


def _collect_equipped_template_keys_by_guest(guests: Iterable[Any]) -> dict[int, list[str]]:
    guest_ids = [guest.pk for guest in guests if is_live_guest_model(guest) and getattr(guest, "pk", None)]
    if not guest_ids:
        return {}

    keys_by_guest: dict[int, list[str]] = {int(guest_id): [] for guest_id in guest_ids}
    for guest_id, template_key in (
        GearItem.objects.filter(guest_id__in=guest_ids).order_by("id").values_list("guest_id", "template__key")
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
            stat_bonus["flat"] = _add_bonus_numbers(stat_bonus["flat"], flat_value)
            stat_bonus["pct"] = _add_bonus_numbers(stat_bonus["pct"], pct_value)


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
            stat_bonus["flat"] = _add_bonus_numbers(stat_bonus["flat"], flat_value)
            stat_bonus["pct"] = _add_bonus_numbers(stat_bonus["pct"], pct_value)


def _load_device_template_payloads(template_keys: Iterable[str]) -> dict[str, tuple[str, Mapping[str, Any]]]:
    unique_keys = {key for key in template_keys if key}
    if not unique_keys:
        return {}

    return {
        key: (name, payload)
        for key, name, payload in ItemTemplate.objects.filter(
            key__in=unique_keys,
            effect_type="equip_device",
        ).values_list("key", "name", "effect_payload")
        if isinstance(payload, Mapping)
    }


def _normalize_troop_device_bonus_source(raw_source: Any) -> TroopDeviceBonusSource | None:
    if not isinstance(raw_source, Mapping):
        return None

    raw_template_key = raw_source.get("template_key")
    if not isinstance(raw_template_key, str) or not raw_template_key.strip():
        return None
    template_key = raw_template_key.strip()

    raw_template_name = raw_source.get("template_name")
    template_name = (
        raw_template_name.strip() if isinstance(raw_template_name, str) and raw_template_name.strip() else template_key
    )

    raw_bonuses = raw_source.get("bonuses")
    bonuses: TroopStatBonuses = {}
    if isinstance(raw_bonuses, Mapping):
        _merge_normalized_troop_device_bonuses(bonuses, raw_bonuses)
    if not bonuses:
        return None
    return {
        "template_key": template_key,
        "template_name": template_name,
        "bonuses": bonuses,
    }


def build_troop_device_bonus_sources_by_guest(guests: Iterable[Any]) -> list[list[TroopDeviceBonusSource]]:
    """Return each guest's equipped device contributions, preserving input order."""

    guest_list = list(guests)
    keys_by_guest = _collect_equipped_template_keys_by_guest(guest_list)
    template_payloads = _load_device_template_payloads(
        template_key for template_keys in keys_by_guest.values() for template_key in template_keys
    )

    sources_by_guest: list[list[TroopDeviceBonusSource]] = []
    for guest in guest_list:
        if is_live_guest_model(guest) and getattr(guest, "pk", None):
            guest_sources: list[TroopDeviceBonusSource] = []
            for template_key in keys_by_guest.get(int(guest.pk), []):
                template_data = template_payloads.get(template_key)
                if template_data is None:
                    continue
                template_name, payload = template_data
                bonuses: TroopStatBonuses = {}
                _merge_troop_stat_bonus_payload(bonuses, payload.get("troop_stat_bonus"))
                if bonuses:
                    guest_sources.append(
                        {
                            "template_key": template_key,
                            "template_name": template_name,
                            "bonuses": bonuses,
                        }
                    )
            sources_by_guest.append(guest_sources)
            continue

        raw_sources = getattr(guest, "troop_device_bonus_sources", None)
        guest_sources = []
        if isinstance(raw_sources, list):
            for raw_source in raw_sources:
                source = _normalize_troop_device_bonus_source(raw_source)
                if source is not None:
                    guest_sources.append(source)
        sources_by_guest.append(guest_sources)

    return sources_by_guest


def _merge_sources_into_bonuses(
    bonuses: TroopStatBonuses,
    sources: Iterable[TroopDeviceBonusSource],
) -> None:
    seen_template_keys: set[str] = set()
    for source in sources:
        template_key = source.get("template_key")
        if isinstance(template_key, str) and template_key:
            if template_key in seen_template_keys:
                continue
            seen_template_keys.add(template_key)
        raw_bonuses = source.get("bonuses")
        if isinstance(raw_bonuses, Mapping):
            _merge_normalized_troop_device_bonuses(bonuses, raw_bonuses)


def build_troop_device_bonuses_from_sources(
    sources: Iterable[TroopDeviceBonusSource],
) -> TroopStatBonuses:
    bonuses: TroopStatBonuses = {}
    _merge_sources_into_bonuses(bonuses, sources)
    return bonuses


def _guest_uses_device_source_schema(guest: Any) -> bool:
    return is_live_guest_model(guest) or bool(getattr(guest, "has_troop_device_bonus_sources", False))


def build_troop_device_bonuses_by_guest(guests: Iterable[Any]) -> list[TroopStatBonuses]:
    """Return each guest's own device contribution, preserving input order."""

    guest_list = list(guests)
    sources_by_guest = build_troop_device_bonus_sources_by_guest(guest_list)
    bonuses_by_guest: list[TroopStatBonuses] = []
    for guest, guest_sources in zip(guest_list, sources_by_guest, strict=True):
        guest_bonuses: TroopStatBonuses = {}
        if _guest_uses_device_source_schema(guest):
            guest_bonuses = build_troop_device_bonuses_from_sources(guest_sources)
        else:
            serialized_bonuses = getattr(guest, "troop_device_bonuses", None)
            if isinstance(serialized_bonuses, Mapping):
                _merge_normalized_troop_device_bonuses(guest_bonuses, serialized_bonuses)
        bonuses_by_guest.append(guest_bonuses)
    return bonuses_by_guest


def build_troop_device_bonus_summary(guests: Iterable[Any]) -> TroopDeviceBonusSummary:
    """Aggregate active devices and cap each template at five effective copies."""

    guest_list = list(guests)
    sources_by_guest = build_troop_device_bonus_sources_by_guest(guest_list)
    bonuses: TroopStatBonuses = {}
    legacy_bonuses: TroopStatBonuses = {}
    device_rows: dict[str, dict[str, Any]] = {}

    for guest, guest_sources in zip(guest_list, sources_by_guest, strict=True):
        if not _guest_uses_device_source_schema(guest):
            serialized_bonuses = getattr(guest, "troop_device_bonuses", None)
            if isinstance(serialized_bonuses, Mapping):
                _merge_normalized_troop_device_bonuses(bonuses, serialized_bonuses)
                _merge_normalized_troop_device_bonuses(legacy_bonuses, serialized_bonuses)
            continue

        seen_template_keys: set[str] = set()
        for source in guest_sources:
            template_key = str(source["template_key"])
            if template_key in seen_template_keys:
                continue
            seen_template_keys.add(template_key)
            row = device_rows.setdefault(
                template_key,
                {
                    "template_key": template_key,
                    "name": str(source["template_name"]),
                    "equipped_count": 0,
                    "effective_count": 0,
                    "bonuses": {},
                },
            )
            row["equipped_count"] += 1
            if row["effective_count"] >= MAX_EFFECTIVE_COPIES_PER_TEMPLATE:
                continue

            raw_source_bonuses = source.get("bonuses")
            if not isinstance(raw_source_bonuses, Mapping):
                continue
            _merge_normalized_troop_device_bonuses(bonuses, raw_source_bonuses)
            _merge_normalized_troop_device_bonuses(row["bonuses"], raw_source_bonuses)
            row["effective_count"] += 1

    devices = sorted(
        (
            {
                **row,
                "capped": row["equipped_count"] > row["effective_count"],
            }
            for row in device_rows.values()
            if row["effective_count"] > 0
        ),
        key=lambda row: (str(row["name"]), str(row["template_key"])),
    )
    if legacy_bonuses:
        devices.append(
            {
                "template_key": "",
                "name": "器械加成（旧快照）",
                "equipped_count": None,
                "effective_count": None,
                "capped": False,
                "bonuses": legacy_bonuses,
            }
        )
    return TroopDeviceBonusSummary(bonuses=bonuses, devices=devices)


def build_troop_device_bonuses(guests: Iterable[Any]) -> TroopStatBonuses:
    return build_troop_device_bonus_summary(guests).bonuses


def _resolve_troop_device_stat_bonus(
    *,
    troop_class: str,
    stat: str,
    device_bonuses: Mapping[str, Mapping[str, Mapping[str, Any]]] | None,
) -> tuple[int | float, int | float]:
    if not device_bonuses:
        return 0, 0

    troop_bonus = device_bonuses.get(troop_class)
    if not isinstance(troop_bonus, Mapping):
        return 0, 0

    stat_bonus = troop_bonus.get(stat)
    if not isinstance(stat_bonus, Mapping):
        return 0, 0

    flat_value = _coerce_positive_number(stat_bonus.get("flat"))
    pct_value = _coerce_positive_number(stat_bonus.get("pct"))
    return flat_value, pct_value


def apply_troop_device_bonus(
    *,
    base_value: int | float,
    troop_class: str,
    stat: str,
    device_bonuses: Mapping[str, Mapping[str, Mapping[str, Any]]] | None,
) -> int | float:
    flat_value, pct_value = _resolve_troop_device_stat_bonus(
        troop_class=troop_class,
        stat=stat,
        device_bonuses=device_bonuses,
    )
    return base_value + flat_value + (base_value * pct_value)


def has_troop_device_stat_bonus(
    *,
    troop_class: str,
    stat: str,
    device_bonuses: Mapping[str, Mapping[str, Mapping[str, Any]]] | None,
) -> bool:
    flat_value, pct_value = _resolve_troop_device_stat_bonus(
        troop_class=troop_class,
        stat=stat,
        device_bonuses=device_bonuses,
    )
    return flat_value > 0 or pct_value > 0


def apply_troop_device_bonus_decimal(
    *,
    base_value: int | float,
    troop_class: str,
    stat: str,
    device_bonuses: Mapping[str, Mapping[str, Mapping[str, Any]]] | None,
) -> Decimal:
    """Apply one bonus with decimal arithmetic for integer state boundaries."""

    flat_value, pct_value = _resolve_troop_device_stat_bonus(
        troop_class=troop_class,
        stat=stat,
        device_bonuses=device_bonuses,
    )
    decimal_base = Decimal(str(base_value))
    return decimal_base + Decimal(str(flat_value)) + (decimal_base * Decimal(str(pct_value)))
