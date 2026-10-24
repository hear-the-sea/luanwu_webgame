from __future__ import annotations

import random


def build_mingjiao_rewards(row: dict, *, config: dict, boss_defeated: bool, rng: random.Random) -> dict:
    snapshots = row.get("guest_snapshots") or []
    low_rarity = len(snapshots) == 3 and all(
        isinstance(snapshot, dict) and snapshot.get("rarity") in {"green", "blue"} for snapshot in snapshots
    )
    eligible = boss_defeated and bool(config.get("enabled", False))
    tier = "low_rarity" if low_rarity else "other"
    fire_seeds = int(config.get(f"{tier}_fire_seeds", 0)) if eligible else 0
    token_chance_bps = int(config.get(f"{tier}_token_chance_bps", 0)) if eligible else 0
    token_quantity = int(eligible and rng.random() < token_chance_bps / 10000)
    return {
        "low_rarity_lineup": low_rarity,
        "fire_seed_quantity": fire_seeds,
        "holy_fire_token_quantity": token_quantity,
        "holy_fire_token_chance_bps": token_chance_bps,
        "item_rewards": {
            key: quantity
            for key, quantity in (
                (config.get("fire_seed_item_key"), fire_seeds),
                (config.get("holy_fire_token_item_key"), token_quantity),
            )
            if key and quantity > 0
        },
    }


def select_rare_drop_item_key(rare_drop: dict, *, rng: random.Random) -> str:
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
    roll = rng.randrange(total_weight)
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


def build_reward_breakdown(
    row: dict,
    *,
    rules: dict,
    boss_defeated: bool,
    rng: random.Random,
) -> dict:
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
        and rng.random() < (int(rules["rare_drop"]["chance_bps"]) / 10000)
    )
    return {
        "participation_coins": participation,
        "damage_coins": damage_coins,
        "rank_coins": rank_coins,
        "clear_coins": clear_coins,
        "total_coins": participation + damage_coins + rank_coins + clear_coins,
        "rare_drop_granted": rare_drop_granted,
        "rare_drop_item_key": select_rare_drop_item_key(rules["rare_drop"], rng=rng) if rare_drop_granted else "",
        "mingjiao": build_mingjiao_rewards(
            row, config=rules["rewards"].get("mingjiao") or {}, boss_defeated=boss_defeated, rng=rng
        ),
    }
