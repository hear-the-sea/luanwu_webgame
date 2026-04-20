"""
Troop device bonus helpers.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
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
    except (TypeError, ValueError):
        return 0
    if parsed <= 0:
        return 0
    if parsed.is_integer():
        return int(parsed)
    return parsed


def _collect_equipped_template_key_counts(guests: Iterable[Any]) -> dict[str, int]:
    guest_ids = [guest.pk for guest in guests if is_live_guest_model(guest) and getattr(guest, "pk", None)]
    if not guest_ids:
        return {}

    return dict(
        Counter(
            key
            for key in GearItem.objects.filter(guest_id__in=guest_ids).values_list("template__key", flat=True)
            if key
        )
    )


def _iter_device_payloads(template_key_counts: Mapping[str, int]) -> Iterable[Mapping[str, Any]]:
    if not template_key_counts:
        return ()

    templates = ItemTemplate.objects.filter(
        key__in=template_key_counts.keys(),
        effect_type="equip_device",
    ).values_list("key", "effect_payload")

    def _payloads() -> Iterable[Mapping[str, Any]]:
        for key, payload in templates:
            if not isinstance(payload, Mapping):
                continue
            for _ in range(template_key_counts.get(key, 0)):
                yield payload

    return _payloads()


def build_troop_device_bonuses(guests: Iterable[Any]) -> TroopStatBonuses:
    bonuses: TroopStatBonuses = {}
    template_key_counts = _collect_equipped_template_key_counts(guests)

    for payload in _iter_device_payloads(template_key_counts):
        raw_troop_bonus = payload.get("troop_stat_bonus")
        if not isinstance(raw_troop_bonus, Mapping):
            continue

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
