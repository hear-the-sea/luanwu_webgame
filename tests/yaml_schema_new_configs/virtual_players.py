import json
from hashlib import sha256

import pytest

from core.utils.yaml_validators.virtual_players import validate_virtual_players


def _messages(result):
    return [error.message for error in result.errors]


def _policy_checksum(policy):
    payload = {key: value for key, value in policy.items() if key != "checksum"}
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _refresh_target_policy_checksum(config):
    policy = config["policies"][str(config["policy_rollout"]["target_version"])]
    policy["checksum"] = _policy_checksum(policy)


def _minimal_v2_config():
    bands = {
        "newbie": [0, 500],
        "junior": [500, 2000],
        "middle": [2000, 8000],
        "senior": [8000, 30000],
        "veteran": [30000, 60000],
        "elite": [60000, 120000],
        "legend": [120000, 240000],
        "mythic": [240000, None],
    }
    growth_profiles = {
        "newbie": {
            "bootstrap_history_age_days": [1, 14],
            "preferred_strength_check_interval_hours": [4, 8],
            "minimum_positive_strength_action_spacing_hours": 4,
            "composite_growth_bps_per_controlled_action_max": 400,
        },
        "junior": {
            "bootstrap_history_age_days": [14, 45],
            "preferred_strength_check_interval_hours": [6, 12],
            "minimum_positive_strength_action_spacing_hours": 6,
            "composite_growth_bps_per_controlled_action_max": 300,
        },
        "middle": {
            "bootstrap_history_age_days": [45, 120],
            "preferred_strength_check_interval_hours": [8, 16],
            "minimum_positive_strength_action_spacing_hours": 8,
            "composite_growth_bps_per_controlled_action_max": 250,
        },
        "senior": {
            "bootstrap_history_age_days": [120, 240],
            "preferred_strength_check_interval_hours": [12, 24],
            "minimum_positive_strength_action_spacing_hours": 12,
            "composite_growth_bps_per_controlled_action_max": 200,
        },
        "veteran": {
            "bootstrap_history_age_days": [240, 360],
            "preferred_strength_check_interval_hours": [14, 24],
            "minimum_positive_strength_action_spacing_hours": 14,
            "composite_growth_bps_per_controlled_action_max": 200,
        },
        "elite": {
            "bootstrap_history_age_days": [360, 540],
            "preferred_strength_check_interval_hours": [18, 30],
            "minimum_positive_strength_action_spacing_hours": 18,
            "composite_growth_bps_per_controlled_action_max": 175,
        },
        "legend": {
            "bootstrap_history_age_days": [540, 720],
            "preferred_strength_check_interval_hours": [24, 36],
            "minimum_positive_strength_action_spacing_hours": 24,
            "composite_growth_bps_per_controlled_action_max": 150,
        },
        "mythic": {
            "bootstrap_history_age_days": [720, 1080],
            "preferred_strength_check_interval_hours": [30, 48],
            "minimum_positive_strength_action_spacing_hours": 30,
            "composite_growth_bps_per_controlled_action_max": 125,
        },
    }
    starter_prestige = {
        "newbie": 400,
        "junior": 1000,
        "middle": 3000,
        "senior": 10000,
        "veteran": 40000,
        "elite": 70000,
        "legend": 140000,
        "mythic": 270000,
    }
    starter_profiles = {
        band: {
            "prestige": prestige,
            "core_building_level": index + 2,
            "max_guest_level": (index + 1) * 5,
            "guest_count": index + 2,
            "arena_lineup_power": (index + 1) * 1000,
            "troop_total": (index + 1) * 500,
            "composite_strength": (index + 1) * 2000,
        }
        for index, (band, prestige) in enumerate(starter_prestige.items())
    }
    policy = {
        "checksum": "",
        "max_development_actions": 1,
        "reference_calibration_min_profiles_per_band": 30,
        "reference_calibration_thresholds": {
            "normalized_wasserstein_max": 0.25,
            "normalized_quantile_deviation_p10_max": 0.35,
            "normalized_quantile_deviation_p50_max": 0.25,
            "normalized_quantile_deviation_p90_max": 0.35,
            "js_divergence_max_bits": 0.10,
            "hard_constraint_violations_max": 0,
            "robust_joint_outlier_rate_max": 0.15,
            "robust_joint_outlier_rate_above_real_max": 0.05,
            "component_fingerprint_collision_rate_max": 0.35,
            "joint_fingerprint_collision_rate_max": 0.15,
            "fingerprint_collision_rate_above_v1_max": 0.0,
            "archetype_standardized_effect_min_absolute": 0.20,
            "archetype_standardized_effect_max_absolute": 0.80,
            "archetype_effect_direction_must_match": True,
            "abandoned_rate_deviation_max": 0.10,
        },
        "reference_calibration_archetype_effects": {
            "rich": {"metric": "mean_building_level", "direction": "higher"},
            "dojo": {"metric": "arena_lineup_power", "direction": "higher"},
            "guard": {"metric": "troop_total", "direction": "higher"},
            "abandoned": {"metric": "composite_strength", "direction": "lower"},
        },
        "reference_calibration_abandoned_features": {
            "underfilled_roster_guest_count_max": 2,
            "stale_gear_level_ratio_max": 0.50,
            "growth_gap_days_min": 30,
        },
        "use_local_reference_when_profiles_gte": 1,
        "borrowed_global_reference_discount_ratio": 0.90,
        "borrowed_global_reference_usage": "composition_anchor_only",
        "borrowed_global_may_raise_sample_tier": False,
        "borrowed_global_may_raise_strength_cap": False,
        "starter_snapshot_scope": "per_prestige_band_conservative_entry_fixture",
        "starter_snapshot_requires_live_player_data": False,
        "zero_local_sample_cap_strategy": "stricter_of_starter_90_percent_and_discounted_global",
        "anchor_k": 5,
        "strength_safety": {
            "no_reference": {
                "starter_snapshot_ratio": 0.90,
                "positive_jitter_bps_max": 0,
                "actions_per_24h_max": 0,
                "growth_bps_per_24h_max": 0,
            },
            "sparse_1_4": {
                "cap_quantile": "p50",
                "composite_cap_ratio": 1.05,
                "component_cap_ratio": 1.10,
                "positive_jitter_bps_max": 0,
                "actions_per_24h_max": 1,
                "growth_bps_per_24h_max": 300,
            },
            "limited_5_29": {
                "cap_quantile": "p75",
                "composite_cap_ratio": 1.10,
                "component_cap_ratio": 1.15,
                "positive_jitter_bps_max": 200,
                "actions_per_24h_max": 2,
                "growth_bps_per_24h_max": 500,
            },
            "sufficient_30_plus": {
                "cap_quantile": "p95",
                "composite_cap_ratio": 1.15,
                "component_cap_ratio": 1.20,
                "positive_jitter_bps_max": 500,
                "actions_per_24h_max": 4,
                "growth_bps_per_24h_max": 1000,
            },
            "arena_acceleration_may_bypass": False,
            "admin_may_bypass": False,
        },
        "prestige_band_growth": {
            "effective_limit_rule": "strictest_of_sample_tier_band_profile_and_domain_constraints",
            "direct_prestige_grant_by_maintenance_allowed": False,
            "profiles": growth_profiles,
            "last_strength_increase_at_required": True,
            "arena_acceleration_may_bypass_band_spacing": False,
            "admin_may_bypass_band_spacing": False,
            "configured_boundaries_crossed_per_controlled_action_max": 1,
            "cross_band_uses_stricter_source_or_destination_limit": True,
            "external_domain_result_may_be_rejected_by_bot_growth_policy": False,
            "bootstrap_fake_per_action_history_records": False,
        },
        "starter_snapshots": {"snapshot_version": 1, "profiles": starter_profiles},
        "gear_upgrade_threshold": [0.08, 0.15],
        "roster_tiers": {
            "core": [0.85, 1.00],
            "secondary": [0.65, 0.85],
            "bench": [0.35, 0.65],
        },
        "troop_mix": {
            "primary": [0.55, 0.75],
            "secondary": [0.15, 0.30],
            "scout": [0.03, 0.10],
        },
        "personas": {
            "balanced": {},
            "rich": {},
            "dojo": {},
            "guard": {},
            "abandoned": {},
        },
    }
    policy["checksum"] = _policy_checksum(policy)
    return {
        "environment_mode": "test",
        "engine_version": 2,
        "rng_version": 1,
        "plan_schema_version": 1,
        "prestige_segmentation": {
            "band_schema_version": 2,
            "boundary_semantics": "lower_inclusive_upper_exclusive",
            "configured_band_count": 8,
            "v2_bands": bands,
            "first_high_band": "veteran",
            "empty_high_band_target_supply": 0,
            "high_band_activation_sources": [
                "active_real_player_presence",
                "explicit_map_search_demand",
                "explicit_arena_demand",
            ],
            "lower_band_supply_counts_for_higher_band": False,
            "cross_band_reactivation_allowed": False,
            "cross_band_instant_strength_promotion_allowed": False,
        },
        "routing": {
            "activation_mode": "direct_after_gate",
            "bootstrap_mode": "legacy_before_gate",
            "maintenance_mode": "legacy_before_gate",
        },
        "policy_rollout": {"target_version": 1, "enabled": False, "rollout_percent": 0},
        "reference_snapshot_catalog": {},
        "policies": {"1": policy},
    }


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
            "guest_max_rarity_by_stage": {1: "green", 7: "blue", 11: "purple"},
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


@pytest.mark.parametrize(
    "field_name",
    ["guest_max_rarity_by_stage", "gear_max_rarity_by_stage"],
)
def test_virtual_players_rejects_boolean_rarity_stage_keys(field_name):
    result = validate_virtual_players({"projection": {field_name: {True: "blue"}}})

    assert not result.is_valid
    assert any("stage must be a positive integer" in error.message for error in result.errors)


@pytest.mark.parametrize(
    "field_name",
    ["guest_max_rarity_by_stage", "gear_max_rarity_by_stage"],
)
def test_virtual_players_rejects_decreasing_rarity_stages(field_name):
    result = validate_virtual_players({"projection": {field_name: {1: "blue", 4: "red"}}})

    assert not result.is_valid
    assert any("rarity must not decrease as stage increases" in error.message for error in result.errors)


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
            "guest_max_rarity_by_stage": {0: "rainbow"},
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
    assert any("guest_max_rarity_by_stage" in message for message in messages)
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
                "tourist": {
                    "weight": 15,
                    "active_days": [7, 21],
                    "abandoned_days": [7, 14],
                },
                "casual": {
                    "weight": 45,
                    "active_days": [30, 90],
                    "abandoned_days": [14, 45],
                },
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
                "tourist": {
                    "weight": 0,
                    "active_days": [21, 7],
                    "abandoned_days": [-1, 14],
                },
                "unknown": {
                    "weight": 0,
                    "active_days": [1, 2],
                    "abandoned_days": [1, 2],
                },
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
    "field",
    [
        "region_floor",
        "region_active_multiplier",
        "global_floor",
        "global_active_multiplier",
    ],
)
def test_virtual_players_rejects_invalid_dynamic_population_fields(field):
    result = validate_virtual_players({"population": {field: True}})

    assert not result.is_valid
    assert any(f"population.{field}" in str(error) for error in result.errors)


def test_virtual_players_accepts_complete_fail_closed_v2_release_input():
    result = validate_virtual_players({"bot_development_v2": _minimal_v2_config()})

    assert result.is_valid, [str(error) for error in result.errors]


def test_virtual_players_accepts_strict_gate_d2_evidence_catalog_entries():
    config = _minimal_v2_config()
    config["reference_snapshot_catalog"] = {
        "3": {
            "schema_version": 1,
            "digest": "a" * 64,
            "artifact_path": "data/virtual_player_reference_snapshots/v3.json",
            "gate_d2_evidence": {
                "1": {
                    "junior": {
                        "schema_version": 3,
                        "digest": "b" * 64,
                    }
                }
            },
        }
    }

    result = validate_virtual_players({"bot_development_v2": config})

    assert result.is_valid, [str(error) for error in result.errors]


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (
            lambda entry: entry.update(schema_version=2),
            "schema_version",
        ),
        (
            lambda entry: entry.update(digest="B" * 64),
            "lowercase SHA-256",
        ),
        (
            lambda entry: entry.update(unknown=True),
            "unknown field",
        ),
    ),
)
def test_virtual_players_rejects_invalid_gate_d2_evidence_catalog_entries(
    mutation,
    message,
):
    config = _minimal_v2_config()
    evidence_entry = {"schema_version": 3, "digest": "b" * 64}
    mutation(evidence_entry)
    config["reference_snapshot_catalog"] = {
        "3": {
            "schema_version": 1,
            "digest": "a" * 64,
            "artifact_path": "data/virtual_player_reference_snapshots/v3.json",
            "gate_d2_evidence": {"1": {"junior": evidence_entry}},
        }
    }

    result = validate_virtual_players({"bot_development_v2": config})

    assert not result.is_valid
    assert any(message in str(error) for error in result.errors)


def test_virtual_players_caps_the_policy_calibration_sample_minimum():
    config = _minimal_v2_config()
    config["policies"]["1"]["reference_calibration_min_profiles_per_band"] = 1001
    _refresh_target_policy_checksum(config)

    result = validate_virtual_players({"bot_development_v2": config})

    assert not result.is_valid
    assert any(
        "reference_calibration_min_profiles_per_band" in str(error) and "must be <= 1000" in str(error)
        for error in result.errors
    )


def test_virtual_players_rejects_unversioned_or_relaxed_calibration_thresholds():
    missing = _minimal_v2_config()
    missing["policies"]["1"].pop("reference_calibration_thresholds")
    _refresh_target_policy_checksum(missing)
    relaxed = _minimal_v2_config()
    relaxed["policies"]["1"]["reference_calibration_thresholds"]["normalized_wasserstein_max"] = 0.30
    _refresh_target_policy_checksum(relaxed)

    missing_result = validate_virtual_players({"bot_development_v2": missing})
    relaxed_result = validate_virtual_players({"bot_development_v2": relaxed})

    assert not missing_result.is_valid
    assert any(
        "missing required field 'reference_calibration_thresholds'" in str(error) for error in missing_result.errors
    )
    assert not relaxed_result.is_valid
    assert any(
        "normalized_wasserstein_max" in str(error) and "must be <= 0.25" in str(error)
        for error in relaxed_result.errors
    )


@pytest.mark.parametrize(
    ("path", "field"),
    [
        ((), "engine_rollout_percent"),
        (("routing",), "bootstrap_enabled"),
        (("policies", "1"), "max_developmnt_actions"),
        (("policies", "1", "strength_safety", "sparse_1_4"), "actions_daily"),
    ],
)
def test_virtual_players_rejects_unknown_v2_fields(path, field):
    config = _minimal_v2_config()
    target = config
    for component in path:
        target = target[component]
    target[field] = 1

    result = validate_virtual_players({"bot_development_v2": config})

    assert not result.is_valid
    assert any(field in str(error) and "unknown field" in str(error) for error in result.errors)


def test_virtual_players_rejects_unknown_root_fields():
    result = validate_virtual_players({"enabled": True, "enabeld": False})

    assert not result.is_valid
    assert any("<root>.enabeld" in str(error) and "unknown field" in str(error) for error in result.errors)


def test_virtual_players_rejects_policy_checksum_mismatch_and_missing_target():
    config = _minimal_v2_config()
    config["policies"]["1"]["checksum"] = "0" * 64
    config["policy_rollout"]["target_version"] = 2

    result = validate_virtual_players({"bot_development_v2": config})
    errors = [str(error) for error in result.errors]

    assert any("checksum" in error and "does not match" in error for error in errors)
    assert any("target policy is missing" in error for error in errors)


def test_virtual_players_rejects_open_rollout_without_positive_percent():
    config = _minimal_v2_config()
    config["policy_rollout"]["enabled"] = True

    result = validate_virtual_players({"bot_development_v2": config})

    assert not result.is_valid
    assert any("must be positive while policy rollout is enabled" in str(error) for error in result.errors)


def test_virtual_players_rejects_maintenance_cutover_before_bootstrap_gate_exit():
    config = _minimal_v2_config()
    config["routing"]["maintenance_mode"] = "v2_cutover"

    result = validate_virtual_players({"bot_development_v2": config})

    assert not result.is_valid
    assert any("before Bootstrap exits Gate D1" in str(error) for error in result.errors)


def test_virtual_players_rejects_invalid_v2_band_boundaries_and_order():
    config = _minimal_v2_config()
    bands = config["prestige_segmentation"]["v2_bands"]
    bands["junior"] = [600, 2000]
    bands["elite"], bands["legend"] = bands.pop("legend"), bands.pop("elite")

    result = validate_virtual_players({"bot_development_v2": config})
    errors = [str(error) for error in result.errors]

    assert any("names and order" in error for error in errors)
    assert any("gapless and non-overlapping" in error for error in errors)


def test_virtual_players_rejects_relaxed_strength_tier():
    config = _minimal_v2_config()
    config["policies"]["1"]["strength_safety"]["sparse_1_4"]["actions_per_24h_max"] = 2

    result = validate_virtual_players({"bot_development_v2": config})

    assert not result.is_valid
    assert any("actions_per_24h_max" in str(error) and "must equal 1" in str(error) for error in result.errors)


@pytest.mark.parametrize(
    "path",
    [
        ("strength_safety", "arena_acceleration_may_bypass"),
        ("strength_safety", "admin_may_bypass"),
        ("prestige_band_growth", "arena_acceleration_may_bypass_band_spacing"),
        ("prestige_band_growth", "admin_may_bypass_band_spacing"),
    ],
)
def test_virtual_players_rejects_arena_and_admin_growth_bypasses(path):
    config = _minimal_v2_config()
    policy = config["policies"]["1"]
    policy[path[0]][path[1]] = True
    _refresh_target_policy_checksum(config)

    result = validate_virtual_players({"bot_development_v2": config})
    errors = [str(error) for error in result.errors]

    assert any(path[1] in error and "must be false" in error for error in errors)
    assert not any("checksum" in error and "does not match" in error for error in errors)


def test_virtual_players_rejects_decreasing_growth_cadence_and_increasing_action_cap():
    config = _minimal_v2_config()
    elite = config["policies"]["1"]["prestige_band_growth"]["profiles"]["elite"]
    elite["minimum_positive_strength_action_spacing_hours"] = 10
    elite["composite_growth_bps_per_controlled_action_max"] = 300

    result = validate_virtual_players({"bot_development_v2": config})
    errors = [str(error) for error in result.errors]

    assert any(
        "minimum_positive_strength_action_spacing_hours" in error and "must not decrease" in error for error in errors
    )
    assert any(
        "composite_growth_bps_per_controlled_action_max" in error and "must not increase" in error for error in errors
    )


@pytest.mark.parametrize("invalid_bound", [1.5, True])
def test_virtual_players_rejects_non_integer_bootstrap_history_after_valid_checksum(
    invalid_bound,
):
    config = _minimal_v2_config()
    config["policies"]["1"]["prestige_band_growth"]["profiles"]["newbie"]["bootstrap_history_age_days"][
        0
    ] = invalid_bound
    _refresh_target_policy_checksum(config)

    result = validate_virtual_players({"bot_development_v2": config})
    errors = [str(error) for error in result.errors]

    assert any("bootstrap_history_age_days[0]" in error and "expected an integer" in error for error in errors)
    assert not any("checksum" in error and "does not match" in error for error in errors)


def test_virtual_players_rejects_starter_snapshot_that_falls_outside_its_band_after_cap():
    config = _minimal_v2_config()
    config["policies"]["1"]["starter_snapshots"]["profiles"]["elite"]["prestige"] = 60000

    result = validate_virtual_players({"bot_development_v2": config})

    assert not result.is_valid
    assert any("90 percent starter prestige" in str(error) for error in result.errors)


def test_virtual_players_rejects_boolean_versions_and_legacy_enrollment_switches():
    config = _minimal_v2_config()
    config["rng_version"] = True
    config["policy_rollout"]["new_profile_percent"] = 50

    result = validate_virtual_players({"bot_development_v2": config})
    errors = [str(error) for error in result.errors]

    assert any("rng_version" in error and "expected an integer" in error for error in errors)
    assert any("new_profile_percent" in error and "unknown field" in error for error in errors)
