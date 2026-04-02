from __future__ import annotations

import random


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
        "rare_drop_item_key": rules["rare_drop"]["item_key"] if rare_drop_granted else "",
    }
