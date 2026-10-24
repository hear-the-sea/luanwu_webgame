import copy
import random

import pytest
from django.test import TestCase

from gameplay.models import ArenaCoopContribution, InventoryItem, ItemTemplate, Message
from gameplay.services.arena.coop_battle import load_runtime_rules_for_event
from gameplay.services.arena.coop_core import run_due_arena_coop_events
from gameplay.services.arena.coop_lifecycle import build_event_snapshots
from gameplay.services.arena.coop_rewards import build_reward_breakdown
from gameplay.services.arena.coop_rules import normalize_arena_coop_rules
from gameplay.services.arena.coop_settlement import format_mingjiao_reward_summary
from tests.arena_services.coop_resolution import _build_coop_message_fixture


class FixedRoll(random.Random):
    def __init__(self, roll):
        super().__init__(0)
        self.roll = roll

    def random(self):
        return self.roll


@pytest.mark.parametrize(
    "fire_seeds,tokens,expected",
    [
        (0, 0, ""),
        (0, 1, "通关奖励：明教圣火令 ×1。"),
        (10, 0, "通关奖励：火种 ×10。"),
        (10, 1, "通关奖励：火种 ×10，明教圣火令 ×1。"),
    ],
)
def test_mingjiao_reward_summary_includes_only_rewards_granted(fire_seeds, tokens, expected):
    summary = format_mingjiao_reward_summary(
        {"mingjiao": {"fire_seed_quantity": fire_seeds, "holy_fire_token_quantity": tokens}}
    )
    assert summary == expected


@pytest.mark.parametrize(
    "rarities,roll,fire_seeds,tokens",
    [
        (["blue", "green", "blue"], 0.4999, 50, 1),
        (["green", "green", "green"], 0.5, 50, 0),
        (["blue", "blue", "blue"], 0.1, 50, 1),
        (["blue", "green", "purple"], 0.0999, 10, 1),
        (["blue", "green", "orange"], 0.1, 10, 0),
        (["green", "green", "gray"], 0.1, 10, 0),
        (["blue", "green"], 0.1, 10, 0),
        ([], 0.1, 10, 0),
    ],
)
def test_mingjiao_clear_rewards_follow_personal_lineup_and_probability_boundaries(rarities, roll, fire_seeds, tokens):
    rules = normalize_arena_coop_rules({"rare_drop": {"enabled": False}})
    rewards = build_reward_breakdown(
        {
            "damage_rank": 5,
            "damage_share_bps": 0,
            "met_minimum_contribution": False,
            "guest_snapshots": [{"rarity": rarity} for rarity in rarities],
        },
        rules=rules,
        boss_defeated=True,
        rng=FixedRoll(roll),
    )["mingjiao"]
    assert rewards["fire_seed_quantity"] == fire_seeds
    assert rewards["holy_fire_token_quantity"] == tokens
    assert rewards["item_rewards"]["mingjiao_fire_seed"] == fire_seeds


def test_failed_coop_does_not_grant_mingjiao_materials():
    rewards = build_reward_breakdown(
        {
            "damage_rank": 1,
            "damage_share_bps": 10000,
            "met_minimum_contribution": True,
            "guest_snapshots": [{"rarity": "blue"}] * 3,
        },
        rules=normalize_arena_coop_rules({}),
        boss_defeated=False,
        rng=FixedRoll(0),
    )["mingjiao"]
    assert rewards["fire_seed_quantity"] == 0
    assert rewards["holy_fire_token_quantity"] == 0
    assert rewards["item_rewards"] == {}


@pytest.mark.django_db
def test_settlement_grants_snapshot_rewards_once_and_includes_reward_message(monkeypatch):
    event, manors, report = _build_coop_message_fixture("mingjiao_rewards")
    for key, name in (("mingjiao_fire_seed", "火种"), ("mingjiao_holy_fire_token", "明教圣火令")):
        ItemTemplate.objects.create(key=key, name=name, effect_type=ItemTemplate.EffectType.TOOL, tradeable=False)
    rules = normalize_arena_coop_rules({})
    # Freeze guaranteed rolls in this event so inventory assertions are deterministic.
    config = rules["rewards"]["mingjiao"]
    config["low_rarity_token_chance_bps"] = config["other_token_chance_bps"] = 10000
    event.reward_snapshot["rewards"]["mingjiao"] = config
    event.save(update_fields=["reward_snapshot"])
    entries = list(event.entries.order_by("id"))
    first_link = entries[0].entry_guests.order_by("id").first()
    first_link.snapshot["rarity"] = "purple"
    first_link.save(update_fields=["snapshot"])
    # The owned guest remains green; eligibility must come from the frozen snapshot.
    assert first_link.guest.rarity == "green"
    monkeypatch.setattr("gameplay.services.arena.coop_core._run_coop_battle_locked", lambda *_args: report)
    with TestCase.captureOnCommitCallbacks(execute=True):
        assert run_due_arena_coop_events(limit=10) == 1
    expected = [10, 50, 50, 50, 50]
    for manor, quantity in zip(manors, expected):
        assert InventoryItem.objects.get(manor=manor, template__key="mingjiao_fire_seed").quantity == quantity
        assert InventoryItem.objects.get(manor=manor, template__key="mingjiao_holy_fire_token").quantity == 1
        message = Message.objects.get(manor=manor, kind=Message.Kind.REWARD)
        assert f"火种 ×{quantity}" in message.body
        assert "明教圣火令 ×1" in message.body
    zero_damage = ArenaCoopContribution.objects.get(entry=entries[-1])
    assert zero_damage.met_minimum_contribution is False
    assert zero_damage.reward_payload["mingjiao"]["fire_seed_quantity"] == 50
    assert run_due_arena_coop_events(limit=10) == 0
    assert InventoryItem.objects.get(manor=manors[0], template__key="mingjiao_fire_seed").quantity == 10


def test_mingjiao_rules_are_frozen_in_reward_snapshot():
    from types import SimpleNamespace

    rules = normalize_arena_coop_rules({})
    enemy, rewards, daily = build_event_snapshots(rules)
    event = SimpleNamespace(enemy_snapshot=enemy, reward_snapshot=rewards, daily_rule_snapshot=daily)
    updated = copy.deepcopy(rules)
    updated["rewards"]["mingjiao"]["low_rarity_fire_seeds"] = 999
    assert load_runtime_rules_for_event(updated, event)["rewards"]["mingjiao"]["low_rarity_fire_seeds"] == 50
    event.reward_snapshot["rewards"].pop("mingjiao")
    assert "mingjiao" not in load_runtime_rules_for_event(updated, event)["rewards"]
