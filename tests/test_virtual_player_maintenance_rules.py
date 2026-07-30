from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from gameplay.services.virtual_player_core.contracts import StrengthBudgetEntry
from gameplay.services.virtual_player_core.maintenance_rules import (
    MAINTENANCE_NO_ACTION_REASON_PRIORITY,
    MaintenanceNoActionReason,
    MaintenanceRuleError,
    bootstrap_historical_age_days,
    effective_growth_limits,
    evaluate_controlled_action,
    next_normal_strength_check_at,
    parse_prestige_band_growth_policy,
)
from gameplay.services.virtual_player_core.projection import PRESTIGE_BANDS, DevelopmentIntent, StrengthSummary
from gameplay.services.virtual_player_core.random_context import RandomContext
from tests.yaml_schema_new_configs.virtual_players import _minimal_v2_config

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
BAND_CADENCE_CASES = (
    ("newbie", 4, 400),
    ("junior", 6, 300),
    ("middle", 8, 250),
    ("senior", 12, 200),
    ("veteran", 14, 200),
    ("elite", 18, 175),
    ("legend", 24, 150),
    ("mythic", 30, 125),
)


def _raw_growth_policy() -> dict:
    return deepcopy(_minimal_v2_config()["policies"]["1"]["prestige_band_growth"])


def _policy():
    return parse_prestige_band_growth_policy(_raw_growth_policy())


def _context(*, sequence: int = 7) -> RandomContext:
    return RandomContext(
        rng_version=1,
        growth_seed=271828,
        engine_version=2,
        plan_schema_version=1,
        policy_version=1,
        maintenance_sequence=sequence,
    )


def _strength(composite: float, *, attack: float, defense: float) -> StrengthSummary:
    return StrengthSummary(composite=composite, components={"attack": attack, "defense": defense})


def _intent(
    *,
    source_band: str = "middle",
    target_band: str = "middle",
    before: StrengthSummary | None = None,
    after: StrengthSummary | None = None,
    violations: tuple[str, ...] = (),
) -> DevelopmentIntent:
    return DevelopmentIntent(
        business_key="training:guest-1",
        action_kind="training",
        source_prestige_band=source_band,
        target_prestige_band=target_band,
        strength_before=before or _strength(100, attack=50, defense=50),
        strength_after=after or _strength(102, attack=51, defense=51),
        utility_score=1,
        constraint_violations=violations,
    )


def _evaluate(
    intent: DevelopmentIntent,
    *,
    now: datetime = NOW,
    last_strength_increase_at: datetime | None = NOW - timedelta(hours=8),
    budget_entries: tuple[StrengthBudgetEntry, ...] = (),
    source_sample_count: int = 30,
    source_strength_cap: StrengthSummary | None = None,
    target_sample_count: int | None = None,
    target_strength_cap: StrengthSummary | None = None,
):
    return evaluate_controlled_action(
        policy=_policy(),
        intent=intent,
        now=now,
        last_strength_increase_at=last_strength_increase_at,
        budget_entries=budget_entries,
        policy_version=1,
        source_sample_count=source_sample_count,
        source_strength_cap=source_strength_cap or _strength(1_000, attack=1_000, defense=1_000),
        target_sample_count=target_sample_count,
        target_strength_cap=target_strength_cap,
    )


def test_growth_policy_parser_freezes_all_eight_cadence_profiles() -> None:
    policy = _policy()

    assert tuple(policy.profiles) == PRESTIGE_BANDS
    assert [profile.bootstrap_history_age_days for profile in policy.profiles.values()] == [
        (1, 14),
        (14, 45),
        (45, 120),
        (120, 240),
        (240, 360),
        (360, 540),
        (540, 720),
        (720, 1080),
    ]
    assert [profile.preferred_strength_check_interval for profile in policy.profiles.values()] == [
        (timedelta(hours=lower), timedelta(hours=upper))
        for lower, upper in (
            (4, 8),
            (6, 12),
            (8, 16),
            (12, 24),
            (14, 24),
            (18, 30),
            (24, 36),
            (30, 48),
        )
    ]
    assert [profile.minimum_positive_strength_action_spacing for profile in policy.profiles.values()] == [
        timedelta(hours=value) for value in (4, 6, 8, 12, 14, 18, 24, 30)
    ]
    assert [profile.composite_growth_bps_per_controlled_action_max for profile in policy.profiles.values()] == [
        400,
        300,
        250,
        200,
        200,
        175,
        150,
        125,
    ]
    with pytest.raises(TypeError):
        policy.profiles["newbie"] = policy.cadence_for("newbie")  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        policy.cadence_for("newbie").prestige_band = "junior"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("admin_may_bypass_band_spacing",), True, "must equal False"),
        (
            ("configured_boundaries_crossed_per_controlled_action_max",),
            True,
            "must equal 1",
        ),
        (
            ("profiles", "newbie", "bootstrap_history_age_days", 0),
            True,
            "non-negative integer",
        ),
        (
            ("profiles", "newbie", "bootstrap_history_age_days", 0),
            1.5,
            "non-negative integer",
        ),
        (
            ("profiles", "newbie", "preferred_strength_check_interval_hours", 0),
            float("nan"),
            "finite non-negative",
        ),
        (
            ("profiles", "elite", "minimum_positive_strength_action_spacing_hours"),
            10,
            "must not decrease",
        ),
        (
            ("profiles", "elite", "composite_growth_bps_per_controlled_action_max"),
            300,
            "must not increase",
        ),
    ],
)
def test_growth_policy_parser_fails_closed_on_invalid_values(path, value, message: str) -> None:
    raw = _raw_growth_policy()
    target = raw
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = value

    with pytest.raises(MaintenanceRuleError, match=message):
        parse_prestige_band_growth_policy(raw)


def test_growth_policy_parser_rejects_missing_and_unknown_profiles() -> None:
    missing = _raw_growth_policy()
    missing["profiles"].pop("mythic")
    with pytest.raises(MaintenanceRuleError, match="names must equal"):
        parse_prestige_band_growth_policy(missing)

    unknown = _raw_growth_policy()
    unknown["profiles"]["newbie"]["shortcut"] = True
    with pytest.raises(MaintenanceRuleError, match="unknown 'shortcut'"):
        parse_prestige_band_growth_policy(unknown)


def test_growth_policy_parser_canonicalizes_persisted_json_profile_order() -> None:
    reordered = _raw_growth_policy()
    reordered["profiles"] = dict(reversed(tuple(reordered["profiles"].items())))

    policy = parse_prestige_band_growth_policy(reordered)

    assert tuple(policy.profiles) == PRESTIGE_BANDS


def test_fixed_random_context_produces_stable_age_and_check_time_in_every_band() -> None:
    policy = _policy()
    context = _context()

    for prestige_band in PRESTIGE_BANDS:
        cadence = policy.cadence_for(prestige_band)
        age = bootstrap_historical_age_days(policy=policy, prestige_band=prestige_band, context=context)
        check_at = next_normal_strength_check_at(
            policy=policy,
            prestige_band=prestige_band,
            context=context,
            now=NOW,
        )

        assert age == bootstrap_historical_age_days(
            policy=policy,
            prestige_band=prestige_band,
            context=context,
        )
        assert cadence.bootstrap_history_age_days[0] <= age <= cadence.bootstrap_history_age_days[1]
        assert check_at == next_normal_strength_check_at(
            policy=policy,
            prestige_band=prestige_band,
            context=context,
            now=NOW,
        )
        assert cadence.preferred_strength_check_interval[0] <= check_at - NOW
        assert check_at - NOW <= cadence.preferred_strength_check_interval[1]


def test_cross_band_effective_limits_take_the_strictest_source_target_and_sample_rules() -> None:
    limits = effective_growth_limits(
        policy=_policy(),
        source_prestige_band="middle",
        target_prestige_band="senior",
        source_sample_count=30,
        target_sample_count=1,
    )

    assert limits.minimum_positive_strength_action_spacing == timedelta(hours=12)
    assert limits.composite_growth_bps_per_controlled_action_max == 200
    assert limits.strength_increasing_actions_per_24h_max == 1
    assert limits.composite_growth_bps_per_24h_max == 300


def test_spacing_rejects_the_instant_before_boundary_and_allows_the_boundary() -> None:
    intent = _intent(after=_strength(102, attack=51, defense=51))
    last_increase = NOW - timedelta(hours=8)

    early = _evaluate(
        intent,
        now=NOW - timedelta(microseconds=1),
        last_strength_increase_at=last_increase,
    )
    boundary = _evaluate(intent, now=NOW, last_strength_increase_at=last_increase)
    late = _evaluate(
        intent,
        now=NOW + timedelta(microseconds=1),
        last_strength_increase_at=last_increase,
    )

    assert early.allowed is False
    assert early.reason is MaintenanceNoActionReason.BAND_SPACING
    assert early.band_spacing_deadline == NOW
    assert boundary.allowed is True
    assert boundary.last_strength_increase_at_after == NOW
    assert boundary.band_spacing_deadline == NOW + timedelta(hours=8)
    assert late.allowed is True


def test_band_action_cap_allows_exact_basis_points_and_rejects_one_more() -> None:
    exact = _evaluate(_intent(after=_strength(102.5, attack=51, defense=51)))
    over = _evaluate(_intent(after=_strength(102.5001, attack=51, defense=51)))

    assert exact.allowed is True
    assert exact.controlled_growth_bps == 250
    assert over.allowed is False
    assert over.controlled_growth_bps == 251
    assert over.reason is MaintenanceNoActionReason.BAND_ACTION_CAP


@pytest.mark.parametrize(("prestige_band", "spacing_hours", "action_cap_bps"), BAND_CADENCE_CASES)
def test_every_band_enforces_spacing_and_action_cap_boundaries(
    prestige_band: str,
    spacing_hours: int,
    action_cap_bps: int,
) -> None:
    before = _strength(10_000, attack=5_000, defense=5_000)
    exact_intent = _intent(
        source_band=prestige_band,
        target_band=prestige_band,
        before=before,
        after=_strength(10_000 + action_cap_bps, attack=5_001, defense=5_000),
    )
    over_intent = _intent(
        source_band=prestige_band,
        target_band=prestige_band,
        before=before,
        after=_strength(10_001 + action_cap_bps, attack=5_001, defense=5_000),
    )
    last_increase = NOW - timedelta(hours=spacing_hours)
    cap = _strength(1_000_000, attack=1_000_000, defense=1_000_000)

    early = _evaluate(
        exact_intent,
        now=NOW - timedelta(microseconds=1),
        last_strength_increase_at=last_increase,
        source_strength_cap=cap,
    )
    exact = _evaluate(
        exact_intent,
        last_strength_increase_at=last_increase,
        source_strength_cap=cap,
    )
    late = _evaluate(
        exact_intent,
        now=NOW + timedelta(microseconds=1),
        last_strength_increase_at=last_increase,
        source_strength_cap=cap,
    )
    over = _evaluate(
        over_intent,
        last_strength_increase_at=last_increase,
        source_strength_cap=cap,
    )

    assert early.skipped_action_reasons == (MaintenanceNoActionReason.BAND_SPACING,)
    assert exact.allowed is True
    assert exact.controlled_growth_bps == action_cap_bps
    assert exact.band_spacing_deadline == NOW + timedelta(hours=spacing_hours)
    assert late.allowed is True
    assert over.skipped_action_reasons == (MaintenanceNoActionReason.BAND_ACTION_CAP,)
    assert over.controlled_growth_bps == action_cap_bps + 1


def test_composite_and_component_caps_are_inclusive_but_never_exceeded() -> None:
    intent = _intent(after=_strength(101, attack=51, defense=51))

    exact = _evaluate(intent, source_strength_cap=_strength(101, attack=51, defense=51))
    composite_over = _evaluate(intent, source_strength_cap=_strength(100.9, attack=60, defense=60))
    component_over = _evaluate(intent, source_strength_cap=_strength(200, attack=50.9, defense=60))

    assert exact.allowed is True
    assert composite_over.reason is MaintenanceNoActionReason.STRENGTH_CAP
    assert component_over.reason is MaintenanceNoActionReason.STRENGTH_CAP


def test_profile_at_any_existing_cap_cannot_raise_another_strength_component() -> None:
    intent = _intent(
        before=_strength(100, attack=50, defense=40),
        after=_strength(100, attack=50, defense=41),
    )

    decision = _evaluate(intent, source_strength_cap=_strength(200, attack=50, defense=100))

    assert decision.allowed is False
    assert decision.reason is MaintenanceNoActionReason.STRENGTH_CAP


def test_component_only_increase_consumes_an_action_and_records_zero_composite_bps() -> None:
    intent = _intent(
        before=_strength(100, attack=50, defense=50),
        after=_strength(100, attack=51, defense=49),
    )

    decision = _evaluate(intent, source_sample_count=1)

    assert decision.allowed is True
    assert decision.strength_increasing is True
    assert decision.controlled_growth_bps == 0
    assert decision.budget_entries_after == (
        StrengthBudgetEntry(applied_at=NOW, positive_growth_bps=0, policy_version=1),
    )
    assert decision.last_strength_increase_at_after == NOW


def test_composite_only_increase_consumes_positive_growth_budget() -> None:
    intent = _intent(
        before=_strength(100, attack=50, defense=50),
        after=_strength(101, attack=50, defense=50),
    )

    decision = _evaluate(intent, source_sample_count=1)

    assert decision.allowed is True
    assert decision.strength_increasing is True
    assert decision.controlled_growth_bps == 100
    assert decision.budget_entries_after == (
        StrengthBudgetEntry(applied_at=NOW, positive_growth_bps=100, policy_version=1),
    )


def test_non_increasing_action_does_not_consume_strength_budget_or_require_spacing_anchor() -> None:
    existing = (StrengthBudgetEntry(applied_at=NOW - timedelta(hours=1), positive_growth_bps=0, policy_version=1),)
    intent = _intent(
        before=_strength(100, attack=50, defense=50),
        after=_strength(99, attack=50, defense=49),
    )

    decision = _evaluate(
        intent,
        last_strength_increase_at=None,
        budget_entries=existing,
        source_sample_count=0,
    )

    assert decision.allowed is True
    assert decision.strength_increasing is False
    assert decision.budget_entries_after == existing
    assert decision.last_strength_increase_at_after is None


def test_daily_action_and_growth_budgets_use_the_frozen_strength_cap_reason() -> None:
    component_only = _intent(
        before=_strength(100, attack=50, defense=50),
        after=_strength(100, attack=51, defense=49),
    )
    sparse_entry = (StrengthBudgetEntry(applied_at=NOW - timedelta(hours=7), positive_growth_bps=0, policy_version=1),)
    action_limited = _evaluate(
        component_only,
        budget_entries=sparse_entry,
        source_sample_count=1,
    )

    limited_entry = (
        StrengthBudgetEntry(
            applied_at=NOW - timedelta(hours=9),
            positive_growth_bps=300,
            policy_version=1,
        ),
    )
    growth_limited = _evaluate(
        _intent(after=_strength(102.5, attack=51, defense=51)),
        budget_entries=limited_entry,
        source_sample_count=5,
    )

    assert action_limited.skipped_action_reasons == (MaintenanceNoActionReason.STRENGTH_CAP,)
    assert growth_limited.skipped_action_reasons == (MaintenanceNoActionReason.STRENGTH_CAP,)


def test_entry_at_exact_24_hour_cutoff_is_pruned_before_budget_consumption() -> None:
    expired = StrengthBudgetEntry(
        applied_at=NOW - timedelta(hours=24),
        positive_growth_bps=300,
        policy_version=1,
    )
    intent = _intent(
        before=_strength(100, attack=50, defense=50),
        after=_strength(100, attack=51, defense=49),
    )

    decision = _evaluate(
        intent,
        budget_entries=(expired,),
        source_sample_count=1,
    )

    assert decision.allowed is True
    assert decision.budget_entries_after == (
        StrengthBudgetEntry(applied_at=NOW, positive_growth_bps=0, policy_version=1),
    )


@pytest.mark.parametrize(
    ("sample_count", "action_cap"),
    ((1, 1), (5, 2), (30, 4)),
)
def test_each_positive_sample_tier_allows_exact_action_cap_and_rejects_one_more(
    sample_count: int,
    action_cap: int,
) -> None:
    existing = tuple(
        StrengthBudgetEntry(
            applied_at=NOW - timedelta(hours=action_cap - index),
            positive_growth_bps=0,
            policy_version=1,
        )
        for index in range(action_cap - 1)
    )
    component_only = _intent(
        before=_strength(100, attack=50, defense=50),
        after=_strength(100, attack=51, defense=49),
    )

    exact = _evaluate(
        component_only,
        budget_entries=existing,
        source_sample_count=sample_count,
    )
    over = _evaluate(
        component_only,
        budget_entries=exact.budget_entries_after,
        source_sample_count=sample_count,
    )

    assert exact.allowed is True
    assert len(exact.budget_entries_after) == action_cap
    assert over.skipped_action_reasons == (MaintenanceNoActionReason.STRENGTH_CAP,)


@pytest.mark.parametrize(
    ("sample_count", "existing_growth_bps", "candidate_growth_bps", "growth_cap"),
    ((1, 0, 300, 300), (5, 200, 300, 500), (30, 700, 300, 1_000)),
)
def test_each_positive_sample_tier_allows_exact_growth_cap_and_rejects_one_more(
    sample_count: int,
    existing_growth_bps: int,
    candidate_growth_bps: int,
    growth_cap: int,
) -> None:
    existing = (
        ()
        if existing_growth_bps == 0
        else (
            StrengthBudgetEntry(
                applied_at=NOW - timedelta(hours=3),
                positive_growth_bps=existing_growth_bps,
                policy_version=1,
            ),
        )
    )
    cap = _strength(1_000_000, attack=1_000_000, defense=1_000_000)
    before = _strength(10_000, attack=5_000, defense=5_000)

    exact = _evaluate(
        _intent(
            source_band="newbie",
            target_band="newbie",
            before=before,
            after=_strength(10_000 + candidate_growth_bps, attack=5_001, defense=5_000),
        ),
        last_strength_increase_at=NOW - timedelta(hours=4),
        budget_entries=existing,
        source_sample_count=sample_count,
        source_strength_cap=cap,
    )
    over = _evaluate(
        _intent(
            source_band="newbie",
            target_band="newbie",
            before=before,
            after=_strength(10_001 + candidate_growth_bps, attack=5_001, defense=5_000),
        ),
        last_strength_increase_at=NOW - timedelta(hours=4),
        budget_entries=existing,
        source_sample_count=sample_count,
        source_strength_cap=cap,
    )

    assert exact.allowed is True
    assert sum(entry.positive_growth_bps for entry in exact.budget_entries_after) == growth_cap
    assert over.skipped_action_reasons == (MaintenanceNoActionReason.STRENGTH_CAP,)


def test_zero_sample_tier_still_blocks_positive_growth() -> None:
    decision = _evaluate(
        _intent(
            before=_strength(100, attack=50, defense=50),
            after=_strength(100, attack=51, defense=49),
        ),
        source_sample_count=0,
    )

    assert decision.allowed is False
    assert decision.reason is MaintenanceNoActionReason.STRENGTH_CAP


def test_cross_band_action_uses_destination_spacing_cap_sample_budget_and_strength_cap() -> None:
    intent = _intent(
        source_band="junior",
        target_band="middle",
        after=_strength(102.5, attack=51, defense=51),
    )

    spacing = _evaluate(
        intent,
        last_strength_increase_at=NOW - timedelta(hours=7),
        target_sample_count=30,
        target_strength_cap=_strength(1_000, attack=1_000, defense=1_000),
    )
    target_sample = _evaluate(
        intent,
        target_sample_count=0,
        target_strength_cap=_strength(1_000, attack=1_000, defense=1_000),
    )
    target_cap = _evaluate(
        intent,
        target_sample_count=30,
        target_strength_cap=_strength(102.4, attack=1_000, defense=1_000),
    )

    assert spacing.reason is MaintenanceNoActionReason.BAND_SPACING
    assert spacing.band_spacing_deadline == NOW + timedelta(hours=1)
    assert target_sample.reason is MaintenanceNoActionReason.STRENGTH_CAP
    assert target_cap.reason is MaintenanceNoActionReason.STRENGTH_CAP


def test_successful_downward_transition_uses_strictest_current_limit_then_target_spacing() -> None:
    intent = _intent(
        source_band="middle",
        target_band="junior",
        after=_strength(102, attack=51, defense=51),
    )
    target_cap = _strength(1_000, attack=1_000, defense=1_000)

    early = _evaluate(
        intent,
        last_strength_increase_at=NOW - timedelta(hours=8) + timedelta(microseconds=1),
        target_sample_count=30,
        target_strength_cap=target_cap,
    )
    applied = _evaluate(
        intent,
        last_strength_increase_at=NOW - timedelta(hours=8),
        target_sample_count=30,
        target_strength_cap=target_cap,
    )

    assert early.reason is MaintenanceNoActionReason.BAND_SPACING
    assert applied.allowed is True
    assert applied.effective_limits is not None
    assert applied.effective_limits.minimum_positive_strength_action_spacing == timedelta(hours=8)
    assert applied.band_spacing_deadline == NOW + timedelta(hours=6)


def test_destination_cap_is_inclusive_and_validates_components_and_shape() -> None:
    intent = _intent(
        source_band="junior",
        target_band="middle",
        after=_strength(101, attack=51, defense=51),
    )

    exact = _evaluate(
        intent,
        target_sample_count=30,
        target_strength_cap=_strength(101, attack=51, defense=51),
    )
    component_over = _evaluate(
        intent,
        target_sample_count=30,
        target_strength_cap=_strength(200, attack=50.9, defense=60),
    )

    assert exact.allowed is True
    assert component_over.reason is MaintenanceNoActionReason.STRENGTH_CAP

    mismatched_components = StrengthSummary(
        composite=200,
        components={"attack": 200, "speed": 200},
    )
    with pytest.raises(MaintenanceRuleError, match="target_strength_cap component keys"):
        _evaluate(
            intent,
            target_sample_count=30,
            target_strength_cap=mismatched_components,
        )
    with pytest.raises(MaintenanceRuleError, match="target_strength_cap"):
        _evaluate(
            intent,
            target_sample_count=30,
            target_strength_cap="invalid",  # type: ignore[arg-type]
        )


def test_crossing_more_than_one_band_is_rejected_before_target_data_is_required() -> None:
    decision = _evaluate(
        _intent(source_band="junior", target_band="senior"),
        target_sample_count=None,
        target_strength_cap=None,
    )

    assert decision.allowed is False
    assert decision.reason is MaintenanceNoActionReason.MULTI_BAND_TRANSITION
    assert decision.effective_limits is None


def test_multi_band_rejection_still_validates_source_sample_and_spacing_anchor() -> None:
    intent = _intent(source_band="junior", target_band="senior")

    with pytest.raises(MaintenanceRuleError, match="sample_count"):
        _evaluate(intent, source_sample_count=-1)
    with pytest.raises(MaintenanceRuleError, match="last_strength_increase_at"):
        _evaluate(intent, last_strength_increase_at=None)


def test_multi_band_rejection_validates_destination_data_when_supplied() -> None:
    intent = _intent(source_band="junior", target_band="senior")

    with pytest.raises(MaintenanceRuleError, match="sample_count"):
        _evaluate(intent, target_sample_count=-1)
    with pytest.raises(MaintenanceRuleError, match="target_strength_cap"):
        _evaluate(
            intent,
            target_strength_cap="invalid",  # type: ignore[arg-type]
        )


def test_existing_domain_violation_is_preserved_as_a_business_no_action() -> None:
    decision = _evaluate(_intent(violations=("guest_busy",)))

    assert decision.allowed is False
    assert decision.reason is MaintenanceNoActionReason.DOMAIN_CONSTRAINT
    assert decision.budget_entries_after == ()


def test_all_applicable_skip_reasons_are_reported_in_stable_priority_order() -> None:
    decision = _evaluate(
        _intent(
            after=_strength(105, attack=55, defense=50),
            violations=("guest_busy",),
        ),
        last_strength_increase_at=NOW - timedelta(hours=1),
        source_sample_count=0,
        source_strength_cap=_strength(101, attack=51, defense=51),
    )

    assert decision.skipped_action_reasons == (
        MaintenanceNoActionReason.DOMAIN_CONSTRAINT,
        MaintenanceNoActionReason.STRENGTH_CAP,
        MaintenanceNoActionReason.BAND_SPACING,
        MaintenanceNoActionReason.BAND_ACTION_CAP,
    )
    assert decision.reason is MaintenanceNoActionReason.DOMAIN_CONSTRAINT


def test_no_action_reason_vocabulary_priority_and_primary_reason_are_frozen() -> None:
    assert MAINTENANCE_NO_ACTION_REASON_PRIORITY == (
        MaintenanceNoActionReason.DOMAIN_CONSTRAINT,
        MaintenanceNoActionReason.STRENGTH_CAP,
        MaintenanceNoActionReason.BAND_SPACING,
        MaintenanceNoActionReason.BAND_ACTION_CAP,
        MaintenanceNoActionReason.MULTI_BAND_TRANSITION,
    )
    decision = _evaluate(
        _intent(
            source_band="junior",
            target_band="senior",
            violations=("guest_busy",),
        )
    )

    assert decision.skipped_action_reasons == (
        MaintenanceNoActionReason.DOMAIN_CONSTRAINT,
        MaintenanceNoActionReason.MULTI_BAND_TRANSITION,
    )
    assert decision.reason is MaintenanceNoActionReason.DOMAIN_CONSTRAINT


def test_every_decision_returns_the_pruned_trailing_budget_window() -> None:
    expired = StrengthBudgetEntry(
        applied_at=NOW - timedelta(hours=24),
        positive_growth_bps=100,
        policy_version=1,
    )
    active = StrengthBudgetEntry(
        applied_at=NOW - timedelta(hours=23),
        positive_growth_bps=100,
        policy_version=1,
    )
    entries = (expired, active)
    non_increasing = _evaluate(
        _intent(after=_strength(99, attack=49, defense=50)),
        last_strength_increase_at=None,
        budget_entries=entries,
    )
    rejected = _evaluate(
        _intent(violations=("guest_busy",)),
        budget_entries=entries,
    )

    assert non_increasing.budget_entries_after == (active,)
    assert rejected.budget_entries_after == (active,)


def test_invalid_cap_inputs_are_not_masked_by_business_no_action() -> None:
    with pytest.raises(MaintenanceRuleError, match="source_strength_cap"):
        _evaluate(
            _intent(violations=("guest_busy",)),
            source_strength_cap="invalid",  # type: ignore[arg-type]
        )

    cross_band_non_growth = _intent(
        source_band="junior",
        target_band="middle",
        before=_strength(100, attack=50, defense=50),
        after=_strength(99, attack=49, defense=50),
        violations=("guest_busy",),
    )
    with pytest.raises(MaintenanceRuleError, match="target_strength_cap"):
        _evaluate(cross_band_non_growth, target_sample_count=30)


def test_cross_band_inputs_fail_closed_when_destination_sample_or_cap_is_missing() -> None:
    intent = _intent(source_band="middle", target_band="senior")

    with pytest.raises(MaintenanceRuleError, match="target_sample_count"):
        _evaluate(intent)
    with pytest.raises(MaintenanceRuleError, match="target_strength_cap"):
        _evaluate(intent, target_sample_count=30)
