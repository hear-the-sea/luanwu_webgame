import pytest

from core.utils.yaml_validators.virtual_players import validate_virtual_players


def _messages(result):
    return [error.message for error in result.errors]


def test_virtual_players_accepts_lifecycle_and_inventory_cap_fields():
    data = {
        "lifecycle": {
            "active_days": [30, 90],
            "abandoned_days": [14, 45],
            "next_growth_hours": [2, 18],
            "empty_hit_stale_threshold": 3,
            "empty_hit_window_hours": 24,
            "stale_no_interaction_days": 30,
        },
        "projection": {
            "guest_template_keys": "__all__",
            "gear_template_keys": "__all__",
            "troop_template_keys": "__all__",
            "technology_keys": "__all__",
            "item_template_keys": "__all_tradeable__",
            "loot_item_template_keys": ["gold_bar"],
            "powerful_item_prestige_chance": [
                {"min_prestige": 0, "chance": 0.0},
                {"min_prestige": 30000, "chance": 0.5},
            ],
            "high_tier_skill_keys": ["stratagem_burst"],
            "high_tier_skill_chance": 0.05,
            "high_tier_skills_per_guest": [1, 1],
            "early_stage_skill_max": 6,
            "early_stage_skill_count": [0, 1],
            "multi_skill_passive_focus_chance": 0.75,
            "gear_max_rarity_by_stage": {1: "green", 7: "blue", 11: "purple"},
            "loot_item_quantity": [1, 3],
            "loot_limits": {"real_attacker_daily_resource_cap": 1000000},
            "rare_item_daily_global_cap": 20,
            "powerful_item_daily_global_cap": 5,
            "powerful_item_min_price": 100000,
            "powerful_item_min_growth_stage": 5,
            "low_stage_powerful_item_chance": 0.03,
        },
    }

    assert validate_virtual_players(data).is_valid


def test_virtual_players_rejects_negative_inventory_cap_fields():
    data = {
        "projection": {
            "rare_item_daily_global_cap": -1,
            "powerful_item_daily_global_cap": -1,
            "powerful_item_min_price": -1,
            "powerful_item_min_growth_stage": -1,
            "powerful_item_prestige_chance": "bad",
            "loot_limits": {"real_attacker_daily_resource_cap": -1},
        }
    }

    result = validate_virtual_players(data)

    assert not result.is_valid
    messages = _messages(result)
    assert any("rare_item_daily_global_cap" in message for message in messages)
    assert any("powerful_item_daily_global_cap" in message for message in messages)
    assert any("powerful_item_min_price" in message for message in messages)
    assert any("powerful_item_min_growth_stage" in message for message in messages)
    assert any("powerful_item_prestige_chance" in message for message in messages)
    assert any("real_attacker_daily_resource_cap" in message for message in messages)


def test_virtual_players_rejects_invalid_inventory_projection_fields():
    data = {
        "projection": {
            "item_template_keys": ["grain", 1],
            "loot_item_template_keys": "gold_bar",
            "high_tier_skill_keys": ["stratagem_burst", 1],
            "high_tier_skill_chance": 1.5,
            "high_tier_skills_per_guest": [1],
            "early_stage_skill_max": -1,
            "early_stage_skill_count": [0, 2],
            "multi_skill_passive_focus_chance": 2,
            "gear_max_rarity_by_stage": {0: "rainbow"},
            "loot_item_quantity": [3, "bad"],
            "rare_item_daily_global_cap": "20",
            "powerful_item_daily_global_cap": 1.5,
            "powerful_item_min_price": "100000",
            "powerful_item_min_growth_stage": "5",
            "powerful_item_prestige_chance": [
                {"min_prestige": "bad", "chance": 1.2},
            ],
            "low_stage_powerful_item_chance": 2,
            "loot_limits": {"real_attacker_daily_resource_cap": "1000000"},
        }
    }

    result = validate_virtual_players(data)

    assert not result.is_valid
    messages = [str(error) for error in result.errors]
    assert any("item_template_keys[1]" in message for message in messages)
    assert any("loot_item_template_keys" in message for message in messages)
    assert any("high_tier_skill_keys[1]" in message for message in messages)
    assert any("high_tier_skill_chance" in message for message in messages)
    assert any("high_tier_skills_per_guest" in message for message in messages)
    assert any("early_stage_skill_max" in message for message in messages)
    assert any("early_stage_skill_count" in message for message in messages)
    assert any("multi_skill_passive_focus_chance" in message for message in messages)
    assert any("gear_max_rarity_by_stage" in message for message in messages)
    assert any("loot_item_quantity" in message for message in messages)
    assert any("rare_item_daily_global_cap" in message for message in messages)
    assert any("powerful_item_daily_global_cap" in message for message in messages)
    assert any("powerful_item_min_price" in message for message in messages)
    assert any("powerful_item_min_growth_stage" in message for message in messages)
    assert any("powerful_item_prestige_chance" in message for message in messages)
    assert any("low_stage_powerful_item_chance" in message for message in messages)
    assert any("real_attacker_daily_resource_cap" in message for message in messages)


def test_virtual_players_rejects_non_positive_inventory_pool_configuration():
    result = validate_virtual_players(
        {
            "projection": {
                "inventory_template_slots_by_archetype": {"rich": 0},
                "inventory_effect_type_weights": {"rich": {"resource_pack": 0}},
            }
        }
    )

    assert not result.is_valid
    errors = [str(error) for error in result.errors]
    assert any("inventory_template_slots_by_archetype.rich" in error for error in errors)
    assert any("inventory_effect_type_weights.rich.resource_pack" in error for error in errors)


def test_virtual_players_rejects_unknown_inventory_and_stage_keys():
    result = validate_virtual_players(
        {
            "prestige_bands": {"junior": [500, 2000]},
            "growth": {"stage_caps": {"junior": 6, "junoir": 7}},
            "projection": {
                "inventory_template_slots_by_archetype": {"wizard": 3},
                "inventory_effect_type_weights": {
                    "wizard": {"resource": 1},
                    "rich": {"mystery_box": 1},
                },
            },
        }
    )

    assert not result.is_valid
    errors = [str(error) for error in result.errors]
    assert any("growth.stage_caps.junoir" in error for error in errors)
    assert any("projection.inventory_template_slots_by_archetype.wizard" in error for error in errors)
    assert any("projection.inventory_effect_type_weights.wizard" in error for error in errors)
    assert any("projection.inventory_effect_type_weights.rich.mystery_box" in error for error in errors)


def test_virtual_players_accepts_data_driven_population_and_persona_fields():
    result = validate_virtual_players(
        {
            "population": {
                "active_player_multiplier": 2,
                "active_window_days": 7,
                "cell_floor": 4,
                "cell_active_multiplier": 2,
                "exploration_supply": 0,
                "retired_reactivation_chance": 0.70,
                "hard_cap": 2000,
            },
            "projection": {
                "active_sample_days": 30,
                "regional_min_sample_size": 5,
                "real_projection_sample_size": 25,
                "strength_quantile_weights": {"p25": 25, "p50": 50, "p75": 25},
            },
            "growth": {
                "catch_up_ratio": 0.25,
                "slowing_ratio_multiplier": 0.5,
                "max_building_step": 2,
                "max_guest_level_step": 3,
                "max_prestige_step": 500,
            },
            "combat_personas": {
                "balanced": {
                    "guest_level_multiplier": 1.0,
                    "guest_count_multiplier": 1.0,
                    "troop_multiplier": 1.0,
                },
                "guard": {
                    "guest_level_multiplier": 0.85,
                    "guest_count_multiplier": 0.85,
                    "troop_multiplier": 1.35,
                },
            },
            "lifecycle_personas": {
                "tourist": {"weight": 15, "active_days": [7, 21], "abandoned_days": [7, 14]},
                "casual": {"weight": 45, "active_days": [30, 90], "abandoned_days": [14, 45]},
            },
        }
    )

    assert result.is_valid


@pytest.mark.parametrize("chance", [True, -0.01, 1.01])
def test_virtual_players_rejects_invalid_retired_reactivation_chance(chance):
    result = validate_virtual_players({"population": {"retired_reactivation_chance": chance}})

    assert not result.is_valid
    assert any("population.retired_reactivation_chance" in str(error) for error in result.errors)


def test_virtual_players_rejects_invalid_data_driven_configuration():
    result = validate_virtual_players(
        {
            "population": {
                "active_window_days": -1,
                "cell_floor": -1,
                "cell_active_multiplier": True,
                "exploration_supply": -1,
            },
            "projection": {
                "active_sample_days": 0,
                "regional_min_sample_size": 0,
                "strength_quantile_weights": {"p25": 0, "p90": 100},
            },
            "growth": {
                "catch_up_ratio": 1.1,
                "slowing_ratio_multiplier": -0.1,
                "max_building_step": 0,
                "max_guest_level_step": True,
                "max_prestige_step": -1,
            },
            "combat_personas": {
                "unknown": {"troop_multiplier": 1.0},
                "guard": {"troop_multiplier": True},
            },
            "lifecycle_personas": {
                "tourist": {"weight": 0, "active_days": [21, 7], "abandoned_days": [-1, 14]},
                "unknown": {"weight": 0, "active_days": [1, 2], "abandoned_days": [1, 2]},
            },
        }
    )

    assert not result.is_valid
    errors = [str(error) for error in result.errors]
    for field in (
        "active_window_days",
        "cell_floor",
        "cell_active_multiplier",
        "exploration_supply",
        "active_sample_days",
        "regional_min_sample_size",
        "p90",
        "catch_up_ratio",
        "slowing_ratio_multiplier",
        "max_building_step",
        "max_guest_level_step",
        "max_prestige_step",
        "combat_personas.unknown",
        "combat_personas.guard.troop_multiplier",
        "lifecycle_personas.tourist.active_days",
        "lifecycle_personas.tourist.abandoned_days",
        "lifecycle_personas.unknown",
        "positive lifecycle weight",
    ):
        assert any(field in error for error in errors), field


@pytest.mark.parametrize(
    "field", ["region_floor", "region_active_multiplier", "global_floor", "global_active_multiplier"]
)
def test_virtual_players_rejects_invalid_dynamic_population_fields(field):
    result = validate_virtual_players({"population": {field: True}})

    assert not result.is_valid
    assert any(f"population.{field}" in str(error) for error in result.errors)
