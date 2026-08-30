from __future__ import annotations

from collections.abc import Callable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

TROOP_CLASS_LABELS = {
    "dao": "刀系",
    "qiang": "枪系",
    "jian": "剑系",
    "quan": "拳系",
    "gong": "弓系",
    "scout": "探子",
}
TROOP_STAT_LABELS = {
    "attack": "攻击",
    "defense": "防御",
    "hp": "生命",
}
TROOP_CORE_STATS = ("attack", "defense", "hp")

BonusAmount = tuple[Decimal, Decimal]
NormalizedStats = dict[str, BonusAmount]


def _positive_decimal(raw: Any) -> Decimal:
    if raw is None or isinstance(raw, bool):
        return Decimal(0)
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)
    if not value.is_finite() or value <= 0:
        return Decimal(0)
    return value


def _format_decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _format_amount(amount: BonusAmount) -> str:
    flat, pct = amount
    parts: list[str] = []
    if flat > 0:
        parts.append(f"+{_format_decimal(flat)}")
    if pct > 0:
        parts.append(f"+{_format_decimal(pct * 100)}%")
    return "、".join(parts)


def _normalize_raw_stats(raw_stats: Any) -> NormalizedStats:
    if not isinstance(raw_stats, Mapping):
        return {}
    normalized: NormalizedStats = {}
    for stat in TROOP_STAT_LABELS:
        flat = _positive_decimal(raw_stats.get(f"{stat}_flat"))
        pct = _positive_decimal(raw_stats.get(f"{stat}_pct"))
        if flat > 0 or pct > 0:
            normalized[stat] = (flat, pct)
    return normalized


def _normalize_aggregated_stats(raw_stats: Any) -> NormalizedStats:
    if not isinstance(raw_stats, Mapping):
        return {}
    normalized: NormalizedStats = {}
    for stat in TROOP_STAT_LABELS:
        raw_bonus = raw_stats.get(stat)
        if not isinstance(raw_bonus, Mapping):
            continue
        flat = _positive_decimal(raw_bonus.get("flat"))
        pct = _positive_decimal(raw_bonus.get("pct"))
        if flat > 0 or pct > 0:
            normalized[stat] = (flat, pct)
    return normalized


def _format_scope_effect(scope: str, stats: NormalizedStats, *, spaced: bool) -> list[str]:
    gap = " " if spaced else ""
    if set(stats) == set(TROOP_CORE_STATS):
        amounts = {stats[stat] for stat in TROOP_CORE_STATS}
        if len(amounts) == 1:
            return [f"{scope}全部属性{gap}{_format_amount(amounts.pop())}"]
    return [
        f"{scope}{TROOP_STAT_LABELS[stat]}{gap}{_format_amount(stats[stat])}"
        for stat in TROOP_STAT_LABELS
        if stat in stats
    ]


def _format_stats_by_class(
    raw_bonuses: Any,
    *,
    normalize_stats: Callable[[Any], NormalizedStats],
    separator: str,
    spaced: bool,
) -> str:
    if not isinstance(raw_bonuses, Mapping):
        return ""
    stats_by_class = {
        troop_class: stats
        for troop_class in TROOP_CLASS_LABELS
        if (stats := normalize_stats(raw_bonuses.get(troop_class)))
    }
    if not stats_by_class:
        return ""

    all_class_stats = list(stats_by_class.values())
    if set(stats_by_class) == set(TROOP_CLASS_LABELS) and all(
        stats == all_class_stats[0] for stats in all_class_stats[1:]
    ):
        return separator.join(_format_scope_effect("全兵种", all_class_stats[0], spaced=spaced))

    parts: list[str] = []
    for troop_class in TROOP_CLASS_LABELS:
        class_stats = stats_by_class.get(troop_class)
        if class_stats:
            parts.extend(_format_scope_effect(TROOP_CLASS_LABELS[troop_class], class_stats, spaced=spaced))
    return separator.join(parts)


def format_raw_troop_device_bonus(raw_bonuses: Any) -> str:
    """Format one item template's raw ``*_flat``/``*_pct`` payload."""

    return _format_stats_by_class(
        raw_bonuses,
        normalize_stats=_normalize_raw_stats,
        separator="、",
        spaced=False,
    )


def format_aggregated_troop_device_bonus(raw_bonuses: Any) -> str:
    """Format a battle report's normalized ``flat``/``pct`` payload."""

    return _format_stats_by_class(
        raw_bonuses,
        normalize_stats=_normalize_aggregated_stats,
        separator="；",
        spaced=True,
    )
