from __future__ import annotations

import random


def select_rare_drop_item_key(rare_drop: dict) -> str:
    choices = rare_drop.get("item_choices") or []
    weighted_choices = [
        (str(choice.get("item_key") or "").strip(), int(choice.get("weight") or 0))
        for choice in choices
        if isinstance(choice, dict)
    ]
    weighted_choices = [(key, weight) for key, weight in weighted_choices if key and weight > 0]
    if not weighted_choices:
        return str(rare_drop.get("item_key") or "").strip()
    total_weight = sum(weight for _key, weight in weighted_choices)
    roll = random.randrange(total_weight)
    for key, weight in weighted_choices:
        if roll < weight:
            return key
        roll -= weight
    return weighted_choices[-1][0]


def rank_contribution_rows(rows: list[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda row: (-row["effective_damage"], -row["boss_damage"], row["joined_at"], row["entry_id"]),
    )


def build_reward_breakdown(row: dict, *, rules: dict, boss_defeated: bool) -> dict:
    participation = int(rules["rewards"]["participation_coins"])
    clear_coins = int(rules["rewards"]["clear_coins"]) if boss_defeated and row["met_minimum_contribution"] else 0
    damage_coins = 0
    for tier in rules["rewards"]["damage_tiers"]:
        if row["damage_share_bps"] >= int(tier["min_share_bps"]):
            damage_coins = int(tier["coins"])
    rank_coins = int(rules["rewards"]["rank_rewards"].get(row["damage_rank"], 0))
    requires_clear = bool(rules["rare_drop"].get("requires_clear", True))
    requires_minimum_contribution = bool(rules["rare_drop"].get("requires_minimum_contribution", True))
    rare_drop_granted = (
        bool(rules["rare_drop"].get("enabled", True))
        and (boss_defeated or not requires_clear)
        and (row["met_minimum_contribution"] or not requires_minimum_contribution)
        and random.random() < (int(rules["rare_drop"]["chance_bps"]) / 10000)
    )
    return {
        "participation_coins": participation,
        "damage_coins": damage_coins,
        "rank_coins": rank_coins,
        "clear_coins": clear_coins,
        "total_coins": participation + damage_coins + rank_coins + clear_coins,
        "rare_drop_granted": rare_drop_granted,
        "rare_drop_item_key": select_rare_drop_item_key(rules["rare_drop"]) if rare_drop_granted else "",
    }
