from __future__ import annotations

from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE_CONFIG_PATH = PROJECT_ROOT / "docs" / "virtual_player_gate_a_acceptance_config_2026-07-27.yaml"
EXPECTED_BANDS = (
    "newbie",
    "junior",
    "middle",
    "senior",
    "veteran",
    "elite",
    "legend",
    "mythic",
)


def _load_acceptance_config() -> dict:
    return yaml.safe_load(ACCEPTANCE_CONFIG_PATH.read_text(encoding="utf-8"))


def test_gate_a_acceptance_config_identity_and_scope_are_frozen() -> None:
    config = _load_acceptance_config()

    assert config["schema_version"] == 13
    assert config["status"] == "gate_a_approved"
    assert (
        config["scope"]
        | {
            "acceptance_only": True,
            "runtime_routing_enabled_by_this_file": False,
            "current_environment": "test",
            "current_environment_class": "non_production",
            "current_environment_is_production": False,
            "production_rollout_in_current_scope": False,
            "evaluate_each_prestige_band_independently": True,
            "fail_closed_on_missing_metric": True,
        }
        == config["scope"]
    )


def test_gate_e_cutover_and_arena_lease_contracts_are_machine_readable() -> None:
    config = _load_acceptance_config()
    gate_e = config["evidence_stages"]["gate_e_maintenance_activation"]

    assert gate_e["readiness_pass_is_gate_exit"] is False
    assert gate_e["gate_exit_requires_runtime_eligible_v1_profiles"] == 0
    assert gate_e["cutover_order"] == [
        "pass_disposable_database_readiness_evidence",
        "enter_v2_cutover_mode_and_stop_v1_and_v2_development_writes",
        "obtain_explicit_confirmation_for_destructive_test_data_rebuild",
        "recreate_disposable_v1_data_and_enroll_retained_test_data",
        "verify_runtime_eligible_v1_profiles_equal_zero",
        "activate_maintenance_v2_for_all_eligible_profiles",
        "exit_gate_e",
    ]
    assert config["arena_reserve"] == {
        "max_no_action_lease_age_hours": 12,
        "activation_gate": "gate_e_maintenance_activation",
        "deadline_source": "reserve_member_created_at",
        "retry_may_extend_deadline": False,
        "busy_result_may_extend_deadline": False,
        "version_change_may_extend_deadline": False,
        "revalidation_may_extend_deadline": False,
    }

    policy = config["policy_release_lifecycle"]
    assert policy["retirement_guard_window_hours"] == 720
    assert policy["retirement_deadline_field"] == "BotPolicyRelease.retire_not_before"
    assert policy["requires_now_gte_retire_not_before"] is True
    assert policy["config_change_may_shorten_existing_deadline"] is False

    reconciliation = config["external_strength_reconciliation"]
    assert reconciliation["statuses"] == [
        "pending_profile",
        "claimed_profile",
        "pending_population",
        "claimed_population",
        "applied",
        "quarantined",
    ]
    assert reconciliation["max_attempts_per_phase"] == 12
    assert reconciliation["claim_fencing"]["finalize_requires_locked_status_and_matching_token"] is True
    assert reconciliation["retry_exhaustion_transition"] == "quarantined"
    assert reconciliation["later_intent_may_pass_unresolved_earlier_intent"] is False
    assert reconciliation["population_phase_attempt_count_is_independent"] is True
    assert reconciliation["profile_identity_storage"] == ("indexed_positive_big_integer_without_foreign_key")
    assert reconciliation["profile_identity_fk_allowed"] is False
    assert reconciliation["completion_timestamp_fields"] == {
        "profile_completed_at": "profile_phase_committed",
        "population_handoff_completed_at": "all_required_population_demands_merged",
        "applied_at": "final_applied_transition",
    }
    assert reconciliation["ambiguous_processed_at_field_allowed"] is False

    population_demand = config["population_recompute_demand"]
    assert population_demand["model"] == "BotPopulationRecomputeDemand"
    assert population_demand["unique_coalescing_key"] == [
        "region",
        "prestige_band",
    ]
    assert population_demand["pending_predicate"] == ("requested_revision_gt_completed_revision")
    assert population_demand["claim_fencing"]["finalize_requires_locked_matching_token_and_claimed_revision"] is True
    assert population_demand["claim_fencing"]["finalize_requires_unexpired_lease"] is True
    assert population_demand["merge_during_claim_remains_pending_after_claim_finalize"] is True
    assert population_demand["merge_may_shorten_existing_failure_backoff"] is False
    assert (
        population_demand["external_reconciliation_applied_requires_all_required_rows_merged_in_same_transaction"]
        is True
    )
    assert population_demand["task_delivery_role"] == "acceleration_only"
    assert population_demand["row_deletion_allowed"] is False
    assert population_demand["consumer_scope"] == ("exactly_claimed_region_and_prestige_band")
    assert population_demand["consumer_may_call_global_population_roll_before_finalize"] is False
    assert population_demand["bounded_batch_continuation"] == {
        "required_when_executable_deficit_remains": True,
        "mechanism": "locked_increment_requested_revision_before_finalize",
        "continuation_available_at": "database_now",
        "completion_still_advances_only_to_claimed_revision": True,
    }

    routing = config["runtime_routing_state"]
    assert routing["current_state_owner"] == "BotRuntimeRoutingState"
    assert routing["transition_semantics"] == ("database_row_lock_and_revision_compare_and_set")
    assert routing["configuration_file_may_mutate_current_state"] is False
    assert routing["calibration_routes"]["storage"] == "strict_json_list"
    assert routing["calibration_routes"]["caller_supplied_item_fields"] == [
        "policy_version",
        "reference_snapshot_version",
        "prestige_band",
    ]
    assert routing["calibration_routes"]["item_fields"] == [
        "policy_version",
        "reference_snapshot_version",
        "prestige_band",
        "policy_checksum",
        "reference_snapshot_digest",
        "evidence_schema_version",
        "evidence_digest",
    ]
    assert routing["calibration_routes"]["proof_fields"] == [
        "policy_checksum",
        "reference_snapshot_digest",
        "evidence_schema_version",
        "evidence_digest",
    ]
    assert routing["calibration_routes"]["proof_owner"] == ("gate_d2_acceptance_workflow")
    assert routing["calibration_routes"]["duplicate_items_allowed"] is False
    assert routing["calibration_routes"]["mutation_uses_same_revision_compare_and_set"] is True
    assert routing["calibration_routes"]["artifact_and_report_preflight_before_routing_row_lock"] is True
    assert routing["calibration_routes"]["runtime_resolver_is_read_only"] is True
    assert (
        routing["calibration_routes"]["invalid_or_drifted_unit_inflight_calibrated_plan_result"]
        == "reject_before_materialization_and_require_replan"
    )
    assert {
        "policy_rollout_target_version",
        "policy_rollout_enabled",
        "policy_rollout_percent",
    }.issubset(routing["required_fields"])
    rollout = routing["policy_rollout"]
    assert rollout["current_state_source"] == "persisted_routing_row"
    assert rollout["mutation_uses_same_revision_compare_and_set"] is True
    assert rollout["enabled_target_is_policy_retirement_reference"] is True
    assert rollout["target_change_or_disable_extends_removed_reference_deadline"] is True
    assert rollout["selection_bucket_inputs"] == [
        "profile_id",
        "target_policy_version",
    ]
    assert rollout["lowering_percent_does_not_downgrade_existing_assignments"] is True

    snapshot_versions = config["reference_snapshot_versioning"]
    assert snapshot_versions["calibration_snapshot_catalog_source"] == ("bot_development_v2.reference_snapshot_catalog")
    assert snapshot_versions["conservative_starter_snapshot_version_is_reference_snapshot_version"] is False
    assert snapshot_versions["policy_version_may_imply_reference_snapshot_version"] is False
    assert snapshot_versions["gate_d2_evidence_catalog_entry_required_fields"] == ["schema_version", "digest"]
    assert snapshot_versions["candidate_report_must_match_cataloged_schema_and_digest"] is True
    assert snapshot_versions["candidate_report_schema_version"] == 3
    assert snapshot_versions["candidate_snapshot_digest_is_report_identity_only"] is False
    assert snapshot_versions["candidate_snapshot_artifact_contract_defined"] is True
    assert snapshot_versions["candidate_artifact_schema_version"] == 2
    assert snapshot_versions["candidate_metric_algorithm_version"] == 2
    assert snapshot_versions["candidate_artifact_raw_records_required"] is True
    assert snapshot_versions["candidate_metrics_recomputed_from_candidate_artifact"] is True
    assert snapshot_versions["candidate_metrics_recomputed_by_versioned_algorithm"] is True
    assert snapshot_versions["candidate_metrics_correctness_status"] == ("implementation_verified")
    assert snapshot_versions["gate_d2_exit_requires_metric_recomputation_or_approved_equivalent_provenance"] is True
    assert snapshot_versions["candidate_generator_attestation_required"] is True
    assert snapshot_versions["candidate_generator_attestation_scheme"] == ("hmac_sha256_v1")
    assert snapshot_versions["candidate_generator_attestation_trusted_key_source"] == "runtime_secret_settings_only"
    assert snapshot_versions["candidate_generator_attestation_key_may_come_from_artifact_report_or_catalog"] is False
    assert snapshot_versions["candidate_generator_attestation_default_trusted_keys"] == 0

    pause = config["environment_activation"]["pause_evaluation"]
    provider = pause["provider"]
    assert pause["current_implementation_status"] == ("implemented_readiness_verified_not_activated")
    assert provider["current_implementation_status"] == ("implemented_readiness_verified_not_activated")
    assert provider["current_environment_backend"] == ("durable_database_event_ledger")
    assert provider["core_task_monitoring_may_supply_safety_truth"] is False
    assert provider["in_process_fallback_allowed"] is False
    assert provider["activation_without_healthy_provider_allowed"] is False
    assert provider["heartbeat_interval_seconds"] == 60
    assert provider["heartbeat_max_gap_seconds"] == 120
    assert "safety_monitor" in provider["required_heartbeat_streams"]
    assert provider["v2_development_write_preflight"] == {
        "owner": "every_v2_development_write_entrypoint",
        "source": "persisted_provider_and_safety_monitor_heartbeat",
        "required_before_business_transaction": True,
        "stale_or_unreadable_result": "pause_without_waiting_for_routing_cas",
        "in_process_cache_may_authorize_write": False,
    }
    assert provider["event_and_window_locking"]["finalization_and_late_event_race_is_serialized"] is True
    assert provider["cleanup"]["last_pause_window_id_referenced_window_may_be_deleted"] is False
    assert pause["maintenance_failure_rate"]["domain_maintenance_result_contains_failed"] is False
    assert pause["maintenance_failure_rate"]["failed_is_observability_attempt_result_only"] is True
    attempt = pause["maintenance_failure_rate"]
    assert attempt["started_event_persisted_before_business_execution"] is True
    assert attempt["attempt_window_owner"] == "utc_window_containing_started_event"
    assert attempt["commit_uncertain_is_hard_violation"] is True
    assert attempt["started_without_terminal_or_commit_uncertain_at_finalize_result"] == "duplicate_or_partial_commit"
    assert attempt["incomplete_attempt_may_count_as_failed"] is False


def test_maintenance_result_and_no_action_reason_contracts_are_machine_readable() -> None:
    contract = _load_acceptance_config()["maintenance_result_contract"]

    assert contract["committed_outcomes"] == ["applied", "no_action"]
    assert contract["sequence_increment_per_committed_cycle"] == 1
    assert contract["non_advancing_outcomes"] == ["busy", "paused", "ineligible"]
    assert contract["schedule_disposition_applies_to_outcomes"] == [
        "applied",
        "no_action",
    ]
    assert contract["triggers"] == {
        "scheduled": {
            "requires_due": True,
            "committed_schedule_disposition": "advance_normal_schedule",
            "committed_deadline_rule": "strictly_later_than_non_null_previous_deadline",
        },
        "arena_acceleration": {
            "requires_due": False,
            "committed_schedule_disposition": "preserve_normal_schedule",
            "committed_deadline_rule": "exact_previous_value",
        },
        "admin": {
            "requires_explicit_due_semantics": True,
            "requires_explicit_schedule_disposition": True,
            "committed_advance_deadline_rule": "non_null_and_different_may_be_earlier_than_previous",
            "committed_preserve_deadline_rule": "exact_previous_value",
        },
    }
    assert contract["non_committed_schedule_rules"] == {
        "busy": "exact_previous_value",
        "paused": "lifecycle_or_safety_pause_contract",
        "ineligible": "lifecycle_contract",
    }
    assert contract["outcome_payloads"] == {
        "applied": {"action_kind": "required_non_empty", "reason": "forbidden"},
        "no_action": {"action_kind": "forbidden", "reason": "required_non_empty"},
        "busy": {"action_kind": "forbidden", "reason": "required_non_empty"},
        "paused": {"action_kind": "forbidden", "reason": "required_non_empty"},
        "ineligible": {"action_kind": "forbidden", "reason": "required_non_empty"},
    }
    reasons = contract["no_action_reasons"]
    assert (
        reasons["values"]
        == reasons["priority"]
        == [
            "domain_constraint",
            "strength_cap",
            "band_spacing",
            "band_action_cap",
            "multi_band_transition",
        ]
    )
    assert reasons["daily_action_or_growth_budget_reason"] == "strength_cap"
    assert reasons["skipped_action_reasons_semantics"] == ("all_applicable_reasons_in_priority_order")
    assert reasons["primary_reason_semantics"] == "first_skipped_action_reason"


def test_v2_prestige_bands_are_gapless_and_have_one_open_terminal_band() -> None:
    config = _load_acceptance_config()
    segmentation = config["prestige_segmentation"]
    bands = segmentation["v2_bands"]

    assert tuple(bands) == EXPECTED_BANDS
    assert segmentation["configured_band_count"] == len(EXPECTED_BANDS)
    previous_upper = 0
    open_band_count = 0
    for lower, upper in bands.values():
        assert lower == previous_upper
        if upper is None:
            open_band_count += 1
            continue
        assert upper > lower
        previous_upper = upper
    assert open_band_count == 1
    assert next(reversed(bands.values()))[1] is None


def test_strength_sample_tiers_are_contiguous_and_keep_frozen_caps() -> None:
    tiers = _load_acceptance_config()["strength_safety"]["sample_tiers"]

    assert tuple(tiers) == ("no_reference", "sparse", "limited", "sufficient")
    assert [(tier["minimum_profiles"], tier.get("maximum_profiles")) for tier in tiers.values()] == [
        (0, 0),
        (1, 4),
        (5, 29),
        (30, None),
    ]
    assert [tier["strength_increasing_actions_per_24h_max"] for tier in tiers.values()] == [0, 1, 2, 4]
    assert [tier["composite_strength_growth_ratio_per_24h_max"] for tier in tiers.values()] == [
        0.0,
        0.03,
        0.05,
        0.10,
    ]


def test_prestige_growth_profiles_are_monotonic() -> None:
    profiles = _load_acceptance_config()["prestige_band_growth"]["profiles"]

    assert tuple(profiles) == EXPECTED_BANDS
    history_lower = [profile["bootstrap_history_age_days"][0] for profile in profiles.values()]
    history_upper = [profile["bootstrap_history_age_days"][1] for profile in profiles.values()]
    spacing = [profile["minimum_positive_strength_action_spacing_hours"] for profile in profiles.values()]
    growth_caps = [profile["composite_growth_bps_per_controlled_action_max"] for profile in profiles.values()]
    assert history_lower == sorted(history_lower)
    assert history_upper == sorted(history_upper)
    assert all(type(value) is int for value in (*history_lower, *history_upper))
    assert spacing == sorted(spacing)
    assert growth_caps == sorted(growth_caps, reverse=True)


def test_gate_a_sampling_and_acceptance_threshold_defaults_are_frozen() -> None:
    config = _load_acceptance_config()

    assert (
        config["sampling"]
        | {
            "sample_limit_per_cohort": 1000,
            "minimum_real_profiles_for_reference_calibration": 30,
            "minimum_v1_fixture_profiles": 30,
            "minimum_reference_profiles_per_prestige_band": 30,
        }
        == config["sampling"]
    )
    assert config["distribution"]["normalized_wasserstein_max"] == 0.25
    assert config["distribution"]["js_divergence"]["max_bits"] == 0.10
    assert config["distribution"]["threshold_source"] == ("BotPolicyRelease.payload.reference_calibration_thresholds")
    assert config["distribution"]["threshold_payload_covered_by_policy_checksum"] is True
    assert config["distribution"]["policy_release_thresholds_may_be_stricter_than_gate_a_baseline"] is True
    assert config["distribution"]["threshold_relaxation_requires_acceptance_contract_revision"] is True
    assert config["economy"]["rare_item_global_daily_cap"] == 8
    assert config["economy"]["powerful_item_global_daily_cap"] == 2
    assert config["performance"]["candidate_scoring_orm_queries_max"] == 0
    assert config["performance"]["deadlocks_max"] == 0
