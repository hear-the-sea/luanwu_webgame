from __future__ import annotations

from core.utils.yaml_schema import validate_arena_rewards, validate_guest_skills, validate_recruitment_rarity_weights
from tests.yaml_schema_new_configs.support import assert_invalid


def test_guest_skills_rejects_non_dict_root():
    result = validate_guest_skills([])
    assert_invalid(result)


def test_guest_skills_rejects_missing_skills():
    result = validate_guest_skills({})
    assert_invalid(result, substring="missing required key 'skills'")


def test_guest_skills_rejects_unknown_rarity():
    data = {"skills": [{"key": "fire_ball", "name": "Fire Ball", "rarity": "legendary"}]}
    result = validate_guest_skills(data)
    assert_invalid(result, substring="rarity")


def test_guest_skills_rejects_invalid_kind():
    data = {"skills": [{"key": "fire_ball", "name": "Fire Ball", "rarity": "green", "kind": "support"}]}
    result = validate_guest_skills(data)
    assert_invalid(result, substring="kind")


def test_guest_skills_rejects_probability_out_of_range():
    data = {"skills": [{"key": "fire_ball", "name": "Fire Ball", "rarity": "green", "base_probability": 1.5}]}
    result = validate_guest_skills(data)
    assert_invalid(result, substring="base_probability")


def test_guest_skills_rejects_non_positive_attribute_requirement():
    data = {"skills": [{"key": "fire_ball", "name": "Fire Ball", "rarity": "green", "required_agility": 0}]}
    result = validate_guest_skills(data)
    assert_invalid(result, substring="required_agility")


def test_guest_skills_rejects_invalid_passive_effect_target_scope():
    data = {
        "skills": [
            {
                "key": "passive_signal",
                "name": "被动信号",
                "rarity": "purple",
                "kind": "passive",
                "passive_config": {
                    "triggers": [
                        {
                            "timing": "round_start",
                            "effects": [{"type": "modify_outgoing_damage", "value": 1.1, "target_scope": "teamwide"}],
                        }
                    ]
                },
            }
        ]
    }
    result = validate_guest_skills(data)
    assert_invalid(result, substring="target_scope")


def test_guest_skills_rejects_blank_passive_effect_target_kind_is():
    data = {
        "skills": [
            {
                "key": "passive_signal",
                "name": "被动信号",
                "rarity": "purple",
                "kind": "passive",
                "passive_config": {
                    "triggers": [
                        {
                            "timing": "round_start",
                            "effects": [{"type": "modify_outgoing_damage", "value": 1.1, "target_kind_is": "   "}],
                        }
                    ]
                },
            }
        ]
    }
    result = validate_guest_skills(data)
    assert_invalid(result, substring="target_kind_is")


def test_guest_skills_rejects_invalid_passive_trigger_chance():
    for chance in (1.5, None, True):
        data = {
            "skills": [
                {
                    "key": "passive_signal",
                    "name": "被动信号",
                    "rarity": "purple",
                    "kind": "passive",
                    "passive_config": {
                        "triggers": [
                            {
                                "timing": "round_start",
                                "chance": chance,
                                "effects": [{"type": "emit_log", "log_name": "被动信号", "message": "触发"}],
                            }
                        ]
                    },
                }
            ]
        }
        result = validate_guest_skills(data)
        assert_invalid(result, substring="chance")


def test_guest_skills_rejects_unknown_passive_trigger_timing():
    data = {
        "skills": [
            {
                "key": "passive_signal",
                "name": "被动信号",
                "rarity": "purple",
                "kind": "passive",
                "passive_config": {
                    "triggers": [
                        {
                            "timing": "before_turn",
                            "effects": [{"type": "emit_log", "log_name": "被动信号", "message": "触发"}],
                        }
                    ]
                },
            }
        ]
    }
    result = validate_guest_skills(data)
    assert_invalid(result, substring="timing")


def test_guest_skills_rejects_unknown_passive_condition_key():
    data = {
        "skills": [
            {
                "key": "passive_signal",
                "name": "被动信号",
                "rarity": "purple",
                "kind": "passive",
                "passive_config": {
                    "triggers": [
                        {
                            "timing": "round_start",
                            "conditions": {"unknown_condition": True},
                            "effects": [{"type": "emit_log", "log_name": "被动信号", "message": "触发"}],
                        }
                    ]
                },
            }
        ]
    }
    result = validate_guest_skills(data)
    assert_invalid(result, substring="unknown_condition")


def test_guest_skills_accepts_new_passive_effect_types():
    data = {
        "skills": [
            {
                "key": "passive_signal",
                "name": "被动信号",
                "rarity": "purple",
                "kind": "passive",
                "passive_config": {
                    "triggers": [
                        {
                            "timing": "round_start",
                            "chance": 0.5,
                            "effects": [
                                {"type": "lose_hp_ratio", "value": 0.1, "nonlethal": True},
                                {"type": "modify_target_weight", "value": 0.5},
                                {"type": "add_true_damage", "value": 0.05, "troop_value_multiplier": 0.25},
                            ],
                        }
                    ]
                },
            }
        ]
    }
    result = validate_guest_skills(data)
    assert result.is_valid


def test_recruitment_rarity_weights_rejects_non_dict_root():
    result = validate_recruitment_rarity_weights("nope")
    assert_invalid(result)


def test_recruitment_rarity_weights_rejects_missing_total_weight():
    result = validate_recruitment_rarity_weights({"weights": {"green": 100}})
    assert_invalid(result, substring="missing required key 'total_weight'")


def test_recruitment_rarity_weights_rejects_missing_weights():
    result = validate_recruitment_rarity_weights({"total_weight": 1000})
    assert_invalid(result, substring="missing required key 'weights'")


def test_recruitment_rarity_weights_rejects_negative_weight():
    data = {"total_weight": 1000, "weights": {"green": -5}}
    result = validate_recruitment_rarity_weights(data)
    assert_invalid(result, substring="weight must be >= 0")


def test_recruitment_rarity_weights_rejects_unknown_rarity():
    data = {"total_weight": 1000, "weights": {"legendary": 100}}
    result = validate_recruitment_rarity_weights(data)
    assert_invalid(result, substring="unknown rarity")


def test_arena_rewards_rejects_non_dict_root():
    result = validate_arena_rewards([])
    assert_invalid(result)


def test_arena_rewards_rejects_missing_rewards():
    result = validate_arena_rewards({})
    assert_invalid(result, substring="missing required key 'rewards'")


def test_arena_rewards_rejects_zero_cost_coins():
    data = {"rewards": [{"key": "grain_pack", "name": "Grain Pack", "cost_coins": 0}]}
    result = validate_arena_rewards(data)
    assert_invalid(result, substring="cost_coins")


def test_arena_rewards_rejects_non_positive_weekly_limit():
    data = {"rewards": [{"key": "weekly_reward", "name": "Weekly Reward", "cost_coins": 100, "weekly_limit": 0}]}

    result = validate_arena_rewards(data)

    assert_invalid(result, substring="weekly_limit")


def test_arena_rewards_rejects_boolean_weekly_limit():
    data = {"rewards": [{"key": "weekly_reward", "name": "Weekly Reward", "cost_coins": 100, "weekly_limit": True}]}

    result = validate_arena_rewards(data)

    assert_invalid(result, substring="weekly_limit")


def test_arena_rewards_rejects_duplicate_keys():
    data = {
        "rewards": [
            {"key": "grain_pack", "name": "Grain Pack", "cost_coins": 80},
            {"key": "grain_pack", "name": "Grain Pack 2", "cost_coins": 100},
        ]
    }
    result = validate_arena_rewards(data)
    assert_invalid(result, substring="duplicate")


def test_arena_rewards_rejects_rotating_blueprint_pool_without_four_unique_blueprints():
    data = {
        "rewards": [
            {
                "key": "blueprint_exchange",
                "name": "本周图纸",
                "cost_coins": 600,
                "rotating_blueprint_pool": {
                    "rarity": "blue",
                    "blueprint_keys": ["blueprint_a", "blueprint_a", "blueprint_c"],
                },
            }
        ]
    }

    result = validate_arena_rewards(data, item_keys={"blueprint_a", "blueprint_c"})

    assert_invalid(result, substring="exactly four unique")


def test_arena_rewards_rejects_random_blueprint_pool_without_rarity():
    data = {
        "rewards": [
            {
                "key": "random_blueprint_exchange",
                "name": "随机图纸",
                "cost_coins": 600,
                "random_blueprint_pool": {"rarity": ""},
            }
        ]
    }

    result = validate_arena_rewards(data)

    assert_invalid(result, substring="rarity")


def test_arena_rewards_rejects_random_blueprint_pool_without_matching_forge_blueprints():
    data = {
        "rewards": [
            {
                "key": "random_blueprint_exchange",
                "name": "随机图纸",
                "cost_coins": 600,
                "random_blueprint_pool": {"rarity": "purple"},
            }
        ]
    }

    result = validate_arena_rewards(data, forge_blueprint_rarities={"blue"})

    assert_invalid(result, substring="no valid forge blueprint")
