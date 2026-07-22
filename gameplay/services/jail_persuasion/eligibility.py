from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any

from core.exceptions import JailError

from .profiles import load_jail_persuasion_profiles

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


def rarity_surcharge(template: Any) -> int:
    rules = load_jail_persuasion_profiles()["recruitment"]
    rarity = str(getattr(template, "rarity", "") or "")
    if rarity == "black" and bool(getattr(template, "is_hermit", False)):
        return int(rules["black_hermit_surcharge"])
    return int(rules["rarity_surcharge"].get(rarity, 0))


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

    if normalized_mode == RECRUIT_NEGOTIATED:
        gold_cost += rarity_surcharge(prisoner.guest_template)
    elif normalized_mode == RECRUIT_HEARTFELT:
        step = int(rule["heart_cost_step"])
        gold_cost += ceil(max(0, heart - 30) / step)
        gold_cost += rarity_surcharge(prisoner.guest_template)
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
