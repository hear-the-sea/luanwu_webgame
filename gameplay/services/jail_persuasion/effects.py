from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .profiles import METHOD_MIGHT, METHOD_ORDER, METHOD_REASON, clamp, load_jail_persuasion_profiles, round_half_up


@dataclass(frozen=True)
class EffectResult:
    outcome: str
    heart_delta: int
    affinity_delta: int
    speaker_loyalty_delta: int = 0


def difficulty_factor(rarity_difficulty_value: int, original_level: int) -> float:
    rules = load_jail_persuasion_profiles()["difficulty"]
    total = min(8, max(0, int(rarity_difficulty_value)) + min(max(0, int(original_level)), 60) // 20)
    return max(float(rules["minimum_factor"]), 1.0 - total * float(rules["factor_per_point"]))


def normalize_speaker_ratio(raw_ratio: float) -> float:
    return min(1.5, max(0.0, float(raw_ratio)))


def _affinity_multiplier(score: int) -> Decimal:
    if score >= 80:
        return Decimal("1.5")
    if score >= 60:
        return Decimal("1.2")
    if score >= 40:
        return Decimal("1.0")
    return Decimal("0.6")


def _speaker_bonus(method: str, ratio: float, archetype: str) -> tuple[int, int]:
    if ratio >= 1.5:
        heart_delta, affinity_delta = -3, 1
    elif ratio >= 1.15:
        heart_delta, affinity_delta = -2, 1
    else:
        heart_delta, affinity_delta = -1, 0

    if method == METHOD_REASON and archetype == "civil":
        affinity_delta += 1
    elif method == METHOD_MIGHT and archetype == "military":
        heart_delta -= 1
        affinity_delta += 1
    return heart_delta, affinity_delta


def resolve_effect(
    *,
    method: str,
    base_score: int,
    stance_method: str,
    taboo_method: str,
    rarity_difficulty_value: int,
    original_level: int,
    same_method_streak: int,
    speaker_ratio: float | None = None,
    speaker_archetype: str = "",
    heart_variation: int = 0,
    affinity_variation: int = 0,
) -> EffectResult:
    if method not in METHOD_ORDER:
        raise ValueError("未知招降手段")
    if heart_variation not in {-1, 0, 1}:
        raise ValueError("心防随机浮动必须在 -1..1")
    if affinity_variation not in {-2, -1, 0, 1, 2}:
        raise ValueError("归心随机浮动必须在 -2..2")

    if taboo_method and method == taboo_method:
        return EffectResult(outcome="taboo", heart_delta=3, affinity_delta=-8)

    if method in {METHOD_REASON, METHOD_MIGHT}:
        if speaker_ratio is None:
            raise ValueError("说客手段必须提供模板基础属性比值")
        normalized_ratio = normalize_speaker_ratio(speaker_ratio)
        if normalized_ratio < 0.70:
            if method == METHOD_REASON:
                return EffectResult("backfire", heart_delta=2, affinity_delta=-4, speaker_loyalty_delta=-1)
            return EffectResult("backfire", heart_delta=3, affinity_delta=-5, speaker_loyalty_delta=-1)
        if normalized_ratio < 0.85:
            return EffectResult("failed", heart_delta=0, affinity_delta=0)

    score = clamp(int(base_score) + (12 if method == stance_method else 0), 0, 100)
    multiplier = _affinity_multiplier(score)
    factor = Decimal(str(difficulty_factor(rarity_difficulty_value, original_level)))
    method_profile = load_jail_persuasion_profiles()["methods"][method]
    heart_delta = Decimal(method_profile["heart_delta"]) * multiplier * factor
    affinity_delta = Decimal(method_profile["affinity_delta"]) * multiplier * factor

    if method in {METHOD_REASON, METHOD_MIGHT}:
        speaker_heart, speaker_affinity = _speaker_bonus(
            method,
            normalize_speaker_ratio(speaker_ratio or 0.0),
            str(speaker_archetype or ""),
        )
        heart_delta += Decimal(speaker_heart)
        affinity_delta += Decimal(speaker_affinity)

    if int(same_method_streak) >= 3:
        heart_delta *= Decimal("0.6")
        affinity_delta *= Decimal("0.6")

    heart_delta += Decimal(heart_variation)
    affinity_delta += Decimal(affinity_variation)
    outcome = "matched" if method == stance_method else "neutral"
    return EffectResult(
        outcome,
        heart_delta=round_half_up(heart_delta),
        affinity_delta=round_half_up(affinity_delta),
    )
