from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from gameplay.services.virtual_player_core.maintenance_rules import (
    GUEST_COUNT_TARGET_MAX,
    MAINTENANCE_NO_ACTION_REASON_PRIORITY,
    MaintenanceNoActionReason,
    MaintenanceRuleError,
    bootstrap_historical_age_days,
    evaluate_controlled_action,
    guest_count_target_for_profile,
    next_normal_strength_check_at,
    parse_prestige_band_growth_policy,
)
from gameplay.services.virtual_player_core.projection import PRESTIGE_BANDS, DevelopmentIntent, StrengthSummary
from gameplay.services.virtual_player_core.random_context import RandomContext
from tests.yaml_schema_new_configs.virtual_players import _minimal_v2_config

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("starter_guest_count", "growth_stage", "roster_focus", "expected"),
    (
        (0, 1, 0.5, 0),
        (4, 1, 0.5, 5),
        (4, 4, 1.0, 7),
        (20, 99, 1.0, GUEST_COUNT_TARGET_MAX),
    ),
)
def test_guest_count_target_for_profile_is_bounded_and_deterministic(
    starter_guest_count,
    growth_stage,
    roster_focus,
    expected,
):
    assert (
        guest_count_target_for_profile(
            starter_guest_count=starter_guest_count,
            growth_stage=growth_stage,
            roster_focus=roster_focus,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("starter_guest_count", -1),
        ("growth_stage", 0),
        ("roster_focus", float("nan")),
    ),
)
def test_guest_count_target_for_profile_rejects_invalid_inputs(field, value):
    arguments = {
        "starter_guest_count": 1,
        "growth_stage": 1,
        "roster_focus": 0.5,
    }
    arguments[field] = value

    with pytest.raises(MaintenanceRuleError):
        guest_count_target_for_profile(**arguments)


def _raw_growth_policy() -> dict:
    return deepcopy(_minimal_v2_config()["policies"]["2"]["prestige_band_growth"])


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
):
    return evaluate_controlled_action(
        policy=_policy(),
        intent=intent,
        now=now,
        last_strength_increase_at=last_strength_increase_at,
    )


def test_growth_policy_parser_freezes_cadence_profiles_without_strength_caps() -> None:
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
    assert not hasattr(policy.cadence_for("newbie"), "minimum_positive_strength_action_spacing")
    assert not hasattr(policy.cadence_for("newbie"), "composite_growth_bps_per_controlled_action_max")
    assert policy.arena_acceleration_bypass.due is True
    with pytest.raises(TypeError):
        policy.profiles["newbie"] = policy.cadence_for("newbie")  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        policy.cadence_for("newbie").prestige_band = "junior"  # type: ignore[misc]


def test_growth_policy_parser_rejects_legacy_cap_fields() -> None:
    raw = _raw_growth_policy()
    raw["profiles"]["newbie"]["composite_growth_bps_per_controlled_action_max"] = 400

    with pytest.raises(MaintenanceRuleError, match="unknown 'composite_growth_bps_per_controlled_action_max'"):
        parse_prestige_band_growth_policy(raw)


def test_growth_policy_parser_rejects_non_boolean_arena_bypass() -> None:
    raw = _raw_growth_policy()
    raw["arena_acceleration_bypass"]["due"] = 1

    with pytest.raises(MaintenanceRuleError, match="due must be a boolean"):
        parse_prestige_band_growth_policy(raw)


def test_growth_policy_parser_rejects_decreasing_cadence() -> None:
    raw = _raw_growth_policy()
    raw["profiles"]["elite"]["bootstrap_history_age_days"] = [10, 20]
    raw["profiles"]["elite"]["preferred_strength_check_interval_hours"] = [2, 4]

    with pytest.raises(MaintenanceRuleError, match="must not decrease"):
        parse_prestige_band_growth_policy(raw)


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


def test_positive_growth_is_allowed_without_reference_daily_or_per_action_caps() -> None:
    decision = _evaluate(
        _intent(
            before=_strength(10_000, attack=5_000, defense=5_000),
            after=_strength(50_000, attack=25_000, defense=25_000),
        ),
        last_strength_increase_at=NOW - timedelta(minutes=1),
    )

    assert decision.allowed is True
    assert decision.reason is None
    assert decision.controlled_growth_bps == 40_000
    assert decision.last_strength_increase_at_after == NOW


def test_strength_growth_does_not_require_a_previous_timestamp() -> None:
    decision = _evaluate(_intent(), last_strength_increase_at=None)

    assert decision.allowed is True
    assert decision.last_strength_increase_at_after == NOW


def test_component_only_increase_is_allowed_without_a_growth_budget_entry() -> None:
    decision = _evaluate(
        _intent(
            before=_strength(100, attack=50, defense=50),
            after=_strength(100, attack=51, defense=49),
        ),
    )

    assert decision.allowed is True
    assert decision.strength_increasing is True
    assert decision.controlled_growth_bps == 0
    assert decision.last_strength_increase_at_after == NOW


def test_non_increasing_action_does_not_update_strength_timestamp() -> None:
    decision = _evaluate(
        _intent(
            before=_strength(100, attack=50, defense=50),
            after=_strength(99, attack=50, defense=49),
        ),
        last_strength_increase_at=None,
    )

    assert decision.allowed is True
    assert decision.strength_increasing is False
    assert decision.last_strength_increase_at_after is None


def test_domain_constraint_remains_a_business_no_action() -> None:
    decision = _evaluate(_intent(violations=("guest_busy",)))

    assert decision.allowed is False
    assert decision.reason is MaintenanceNoActionReason.DOMAIN_CONSTRAINT


def test_crossing_more_than_one_band_is_rejected_before_growth_rules() -> None:
    decision = _evaluate(_intent(source_band="junior", target_band="senior"))

    assert decision.allowed is False
    assert decision.reason is MaintenanceNoActionReason.MULTI_BAND_TRANSITION


def test_multi_band_and_domain_reasons_keep_stable_priority() -> None:
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


def test_no_action_reason_vocabulary_contains_no_strength_cap_reason() -> None:
    assert MAINTENANCE_NO_ACTION_REASON_PRIORITY == (
        MaintenanceNoActionReason.DOMAIN_CONSTRAINT,
        MaintenanceNoActionReason.MULTI_BAND_TRANSITION,
    )
    assert {reason.value for reason in MaintenanceNoActionReason} == {
        "domain_constraint",
        "multi_band_transition",
    }
