from __future__ import annotations

import copy

import pytest

from gameplay.models import ArenaCoopEvent, ItemTemplate
from gameplay.services.arena.coop_battle import load_runtime_rules_for_event
from gameplay.services.arena.coop_lifecycle import build_event_snapshots
from gameplay.services.arena.coop_settlement import format_rare_drop_summary


def test_lifecycle_module_build_event_snapshots_returns_detached_copies():
    rules = {
        "enemy": {"boss": {"template_key": "boss_a"}},
        "rewards": {"participation_coins": 10},
        "rare_drop": {"item_key": "equip_a"},
        "registration": {"daily_participation_limit": 2},
        "contribution": {"minimum_share_bps": 500},
    }

    enemy_snapshot, reward_snapshot, daily_rule_snapshot = build_event_snapshots(rules)
    enemy_snapshot["boss"]["template_key"] = "boss_b"
    reward_snapshot["rewards"]["participation_coins"] = 20
    daily_rule_snapshot["registration"]["daily_participation_limit"] = 5

    assert rules["enemy"]["boss"]["template_key"] == "boss_a"
    assert rules["rewards"]["participation_coins"] == 10
    assert rules["registration"]["daily_participation_limit"] == 2


@pytest.mark.django_db
def test_battle_module_load_runtime_rules_for_event_merges_snapshots():
    base_rules = {
        "enemy": {"boss": {"template_key": "boss_a", "display_name": "BossA"}},
        "rewards": {"participation_coins": 10},
        "rare_drop": {"item_key": "equip_a"},
        "registration": {"daily_participation_limit": 2},
        "contribution": {"minimum_share_bps": 500},
    }
    base_rules_snapshot = copy.deepcopy(base_rules)
    event = ArenaCoopEvent(
        enemy_snapshot={"boss": {"display_name": "BossB"}},
        reward_snapshot={"rewards": {"participation_coins": 20}},
        daily_rule_snapshot={"contribution": {"minimum_share_bps": 800}},
    )

    merged = load_runtime_rules_for_event(base_rules, event)

    assert merged["enemy"]["boss"]["template_key"] == "boss_a"
    assert merged["enemy"]["boss"]["display_name"] == "BossB"
    assert merged["rewards"]["participation_coins"] == 20
    assert merged["contribution"]["minimum_share_bps"] == 800
    assert base_rules == base_rules_snapshot


def test_battle_module_legacy_item_key_snapshot_does_not_inherit_current_choices():
    base_rules = {
        "enemy": {},
        "rewards": {},
        "rare_drop": {
            "item_key": "current_fallback",
            "item_choices": [{"item_key": "current_blueprint", "weight": 1}],
        },
        "registration": {},
        "contribution": {},
    }
    event = ArenaCoopEvent(
        reward_snapshot={
            "rare_drop": {
                "item_key": "legacy_equipment",
                "chance_bps": 10,
            }
        }
    )

    merged = load_runtime_rules_for_event(base_rules, event)

    assert merged["rare_drop"]["item_key"] == "legacy_equipment"
    assert merged["rare_drop"]["item_choices"] == []


@pytest.mark.django_db
def test_settlement_module_formats_rare_drop_summary_with_item_name():
    ItemTemplate.objects.create(
        key="equip_split_drop",
        name="拆分测试掉落",
        effect_type=ItemTemplate.EffectType.TOOL,
    )

    assert format_rare_drop_summary("equip_split_drop") == "掉落稀有奖励：拆分测试掉落"
    assert format_rare_drop_summary("") == "未掉落稀有奖励"
