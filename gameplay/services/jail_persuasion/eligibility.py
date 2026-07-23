from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import ceil
from typing import Any

from core.exceptions import JailError

from .profiles import load_jail_persuasion_profiles, round_half_up

RECRUIT_STANDARD = "standard"
RECRUIT_NEGOTIATED = "negotiated"
RECRUIT_HEARTFELT = "heartfelt"
RECRUITMENT_MODES = (RECRUIT_STANDARD, RECRUIT_NEGOTIATED, RECRUIT_HEARTFELT)


@dataclass(frozen=True)
class RecruitmentOffer:
    mode: str
    eligible: bool
    gold_cost: int
    initial_loyalty: int
    heart_max: int
    affinity_min: int


def recruitment_offer(prisoner: Any, mode: str) -> RecruitmentOffer:
    normalized_mode = str(mode or "").strip()
    if normalized_mode not in RECRUITMENT_MODES:
        raise JailError("未知的归附方式")

    rules = load_jail_persuasion_profiles()["recruitment"]
    rule = rules[normalized_mode]
    heart = int(getattr(prisoner, "loyalty", 0) or 0)
    affinity = int(getattr(prisoner, "affinity", 0) or 0)
    eligible = heart <= int(rule["heart_max"]) and affinity >= int(rule["affinity_min"])
    gold_cost = int(rule["base_gold_cost"])

    if normalized_mode == RECRUIT_HEARTFELT:
        step = int(rule["heart_cost_step"])
        gold_cost += ceil(max(0, heart - 30) / step)
        if int(getattr(prisoner, "milestone_stage", 0) or 0) >= 2:
            gold_cost -= int(rule["milestone_discount"])
        gold_cost = max(1, gold_cost)

    return RecruitmentOffer(
        mode=normalized_mode,
        eligible=eligible,
        gold_cost=max(0, gold_cost),
        initial_loyalty=int(rule["initial_loyalty"]),
        heart_max=int(rule["heart_max"]),
        affinity_min=int(rule["affinity_min"]),
    )


def recruitment_success_percent(prisoner: Any, mode: str) -> int:
    normalized_mode = str(mode or "").strip()
    if normalized_mode not in RECRUITMENT_MODES:
        raise JailError("未知的归附方式")

    rules = load_jail_persuasion_profiles()["recruitment"]
    probability_rules = rules["success_probability"]
    mode_rules = probability_rules[normalized_mode]
    heart = max(0, min(100, int(getattr(prisoner, "loyalty", 0) or 0)))
    affinity = max(0, min(100, int(getattr(prisoner, "affinity", 0) or 0)))

    if normalized_mode == RECRUIT_STANDARD:
        heart = min(heart, 30)
        progress = Decimal(30 - heart) / Decimal(30)
        probability = Decimal(mode_rules["minimum"]) + progress * Decimal(mode_rules["maximum"] - mode_rules["minimum"])
    elif normalized_mode == RECRUIT_NEGOTIATED:
        heart = min(heart, 45)
        affinity = max(60, affinity)
        heart_progress = Decimal(45 - heart) / Decimal(45)
        affinity_progress = Decimal(affinity - 60) / Decimal(40)
        heart_bonus = round_half_up(heart_progress * Decimal(mode_rules["heart_bonus_max"]))
        affinity_bonus = round_half_up(affinity_progress * Decimal(mode_rules["affinity_bonus_max"]))
        probability = Decimal(int(mode_rules["base"]) + heart_bonus + affinity_bonus)
    else:
        probability = Decimal(mode_rules["minimum"]) + Decimal(100 - heart) / Decimal(100) * Decimal(
            mode_rules["maximum"] - mode_rules["minimum"]
        )

    template = prisoner.guest_template
    rarity = str(getattr(template, "rarity", "") or "")
    if rarity == "black" and bool(getattr(template, "is_hermit", False)):
        penalty = int(probability_rules["black_hermit_penalty"])
    else:
        penalty = int(probability_rules["rarity_penalty"].get(rarity, 0))

    rounded = round_half_up(probability - Decimal(penalty))
    return max(int(probability_rules["final_minimum"]), min(int(probability_rules["final_maximum"]), rounded))
