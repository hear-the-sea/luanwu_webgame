"""招募保底的候选选择和计数规则；持久化由批次事务统一处理。"""

from __future__ import annotations

import random

from core.config import RECRUITMENT

from ..models import GuestRarity, GuestTemplate, RecruitmentPity


def apply_candidate_pity(
    template: GuestTemplate,
    *,
    pity: RecruitmentPity,
    templates_by_rarity: dict[str, list[GuestTemplate]],
    excluded_ids: set[int],
    rng: random.Random,
) -> tuple[GuestTemplate, str]:
    """仅从尚可招募的指定稀有度中兑现；池耗尽时保留资格。"""
    purple_due = pity.purple_misses >= RECRUITMENT.PURPLE_PITY_LIMIT - 1
    orange_due = pity.orange_misses >= RECRUITMENT.ORANGE_PITY_LIMIT - 1
    marker = ""

    def available(rarity: str) -> list[GuestTemplate]:
        return [entry for entry in templates_by_rarity.get(rarity, []) if entry.id not in excluded_ids]

    if orange_due:
        if template.rarity == GuestRarity.ORANGE:
            marker = GuestRarity.ORANGE
        else:
            options = available(GuestRarity.ORANGE)
            if options:
                template = rng.choice(options)
                marker = GuestRarity.ORANGE
    if not marker and purple_due:
        if template.rarity in (GuestRarity.PURPLE, GuestRarity.ORANGE):
            marker = GuestRarity.PURPLE
        else:
            options = available(GuestRarity.PURPLE) or available(GuestRarity.ORANGE)
            if options:
                template = rng.choice(options)
                marker = GuestRarity.PURPLE

    if template.rarity == GuestRarity.ORANGE:
        pity.orange_misses = 0
        pity.purple_misses = 0
    else:
        pity.orange_misses = min(pity.orange_misses + 1, RECRUITMENT.ORANGE_PITY_LIMIT)
        pity.purple_misses = (
            0 if template.rarity == GuestRarity.PURPLE else min(pity.purple_misses + 1, RECRUITMENT.PURPLE_PITY_LIMIT)
        )
    return template, marker
