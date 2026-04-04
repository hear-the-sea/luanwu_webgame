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


def test_arena_rewards_rejects_duplicate_keys():
    data = {
        "rewards": [
            {"key": "grain_pack", "name": "Grain Pack", "cost_coins": 80},
            {"key": "grain_pack", "name": "Grain Pack 2", "cost_coins": 100},
        ]
    }
    result = validate_arena_rewards(data)
    assert_invalid(result, substring="duplicate")
