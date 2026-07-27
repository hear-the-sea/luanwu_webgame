from __future__ import annotations

import random

from battle.execution import _resolve_battle_rng
from battle.random_context import (
    CURRENT_RNG_VERSION,
    MAX_PERSISTED_SEED,
    RNG_STREAM_COMBAT,
    RNG_STREAM_LOOT,
    RNG_STREAM_RARE_DROP,
    BattleRandomContext,
)
from gameplay.services.arena.coop_rewards import build_reward_breakdown


def test_rng_source_supplies_replayable_base_seed_when_explicit_seed_is_absent():
    rng_source = random.Random(20260727)
    expected_source = random.Random(20260727)
    expected_seed = expected_source.randrange(1, MAX_PERSISTED_SEED + 1)

    context, combat_rng = _resolve_battle_rng(
        None,
        rng_source,
        rng_version=CURRENT_RNG_VERSION,
    )
    expected_combat_rng = BattleRandomContext.create(expected_seed).rng(RNG_STREAM_COMBAT)

    assert context.base_seed == expected_seed
    assert [combat_rng.random() for _ in range(5)] == [expected_combat_rng.random() for _ in range(5)]


def test_explicit_seed_takes_precedence_without_consuming_rng_source():
    rng_source = random.Random(12345)
    original_state = rng_source.getstate()

    context, _combat_rng = _resolve_battle_rng(
        98765,
        rng_source,
        rng_version=CURRENT_RNG_VERSION,
    )

    assert context.base_seed == 98765
    assert rng_source.getstate() == original_state


def test_rare_drop_replays_and_is_not_perturbed_by_other_substreams():
    row = {
        "damage_rank": 1,
        "damage_share_bps": 5000,
        "met_minimum_contribution": True,
    }
    rules = {
        "rewards": {
            "participation_coins": 30,
            "clear_coins": 40,
            "damage_tiers": [{"min_share_bps": 1000, "coins": 20}],
            "rank_rewards": {1: 80},
        },
        "rare_drop": {
            "enabled": True,
            "chance_bps": 10000,
            "requires_clear": True,
            "requires_minimum_contribution": True,
            "item_choices": [
                {"item_key": "rare_a", "weight": 1},
                {"item_key": "rare_b", "weight": 2},
            ],
        },
    }
    first_context = BattleRandomContext.create(1618033)
    expected = build_reward_breakdown(
        row,
        rules=rules,
        boss_defeated=True,
        rng=first_context.rng(RNG_STREAM_RARE_DROP),
    )

    replay_context = BattleRandomContext.create(1618033)
    combat_rng = replay_context.rng(RNG_STREAM_COMBAT)
    loot_rng = replay_context.rng(RNG_STREAM_LOOT)
    for _ in range(100):
        combat_rng.random()
        loot_rng.random()
    replayed = build_reward_breakdown(
        row,
        rules=rules,
        boss_defeated=True,
        rng=replay_context.rng(RNG_STREAM_RARE_DROP),
    )

    assert replayed == expected
