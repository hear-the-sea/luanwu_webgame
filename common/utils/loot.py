from __future__ import annotations

import logging
import random
import secrets
from typing import Dict

logger = logging.getLogger(__name__)


def _create_default_rng() -> random.Random:
    return random.Random(secrets.randbits(128))


def _add_drop(drops: Dict[str, int], key: str, count: int) -> None:
    if count <= 0:
        return
    drops[key] = drops.get(key, 0) + int(count)


def _parse_numeric_drop_value(value: float | int) -> tuple[float | None, int]:
    if value >= 1:
        return None, int(value)
    return float(value), 1


def _parse_integer_drop_count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return int(value)
    except (OverflowError, TypeError, ValueError):
        return None


def _parse_drop_count(value: object) -> int | tuple[int, int] | None:
    if isinstance(value, dict):
        raw_minimum = value.get("min", value.get("minimum"))
        raw_maximum = value.get("max", value.get("maximum"))
        minimum = _parse_integer_drop_count(raw_minimum)
        maximum = _parse_integer_drop_count(raw_maximum)
        if minimum is None or maximum is None:
            return None
        if minimum < 0 or maximum < minimum:
            return None
        return minimum if minimum == maximum else (minimum, maximum)
    return _parse_integer_drop_count(value)


def _parse_dict_drop_value(value: dict) -> tuple[float | None, int | tuple[int, int]]:
    chance_raw = value.get("chance", value.get("probability"))
    count_raw = value.get("count", value.get("quantity", value.get("amount")))

    try:
        chance = float(chance_raw) if chance_raw is not None else None
    except (TypeError, ValueError):
        chance = None

    count = _parse_drop_count(count_raw)
    if count is None:
        count = 1
    return chance, count


def _should_drop(chance: float | None, rng: random.Random) -> bool:
    if chance is None:
        return True
    if chance <= 0:
        return False
    if chance >= 1:
        return True
    return rng.random() < chance


def _resolve_drop_entry(value: float | int | dict) -> tuple[float | None, int | tuple[int, int]] | None:
    if isinstance(value, dict):
        chance, count = _parse_dict_drop_value(value)
        return chance, count

    if isinstance(value, (int, float)):
        chance, count = _parse_numeric_drop_value(value)
        if chance is not None and chance <= 0:
            return None
        return chance, count

    return None


def _normalize_choice_entries(choices: object) -> list[tuple[str, int]]:
    if not isinstance(choices, list):
        return []

    normalized: list[tuple[str, int]] = []
    for entry in choices:
        if isinstance(entry, str):
            key = entry.strip()
            weight = 1
        elif isinstance(entry, dict):
            raw_key = entry.get("key") or entry.get("item_key") or entry.get("template_key")
            key = str(raw_key or "").strip()
            try:
                weight = int(entry.get("weight", 1) or 0)
            except (TypeError, ValueError):
                weight = 0
        else:
            continue

        if key and weight > 0:
            normalized.append((key, weight))

    return normalized


def _pick_weighted_choice(choices: list[tuple[str, int]], rng: random.Random) -> str | None:
    if not choices:
        return None

    total_weight = sum(weight for _key, weight in choices)
    if total_weight <= 0:
        return None

    roll = rng.random() * total_weight
    cumulative = 0
    chosen = choices[-1][0]
    for key, weight in choices:
        cumulative += weight
        if roll < cumulative:
            chosen = key
            break
    return chosen


def resolve_drop_rewards(drop_table: Dict[str, float | int | dict], rng: random.Random | None = None) -> Dict[str, int]:
    """
    Resolve a YAML-like drop table into concrete drops.

    Rules:
    - value >= 1: guaranteed amount
    - value < 1: probability to drop 1
    - value is dict: supports {"chance"/"probability", "count"/"quantity"/"amount"}
    - dict may additionally include "choices" for weighted random selection of the actual drop key

    Args:
        drop_table: 掉落配置表
        rng: 随机数生成器（如果不传入，将使用加密安全的种子创建）

    Returns:
        Dict[str, int]: 实际掉落物品及数量
    """
    # 使用加密安全的随机种子，防止掉落结果被预测
    if rng is None:
        rng = _create_default_rng()
    drops: Dict[str, int] = {}

    for key, value in (drop_table or {}).items():
        if value is None:
            continue

        resolved = _resolve_drop_entry(value)
        if resolved is None:
            continue
        chance, count = resolved
        if _should_drop(chance, rng):
            drop_key = key
            if isinstance(value, dict):
                choice_entries = _normalize_choice_entries(value.get("choices"))
                if choice_entries:
                    chosen_key = _pick_weighted_choice(choice_entries, rng)
                    if not chosen_key:
                        continue
                    drop_key = chosen_key
            amount = rng.randint(*count) if isinstance(count, tuple) else count
            _add_drop(drops, drop_key, amount)

    return drops
