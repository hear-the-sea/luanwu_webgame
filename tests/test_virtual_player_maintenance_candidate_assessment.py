from __future__ import annotations

from datetime import UTC, date, datetime

from gameplay.services.virtual_player_core.maintenance_candidate_assessment import (
    CandidateAssessment,
    select_candidate_assessment,
)
from gameplay.services.virtual_player_core.maintenance_resources import ResourcePlanningSnapshot
from gameplay.services.virtual_player_core.projection import DevelopmentIntent, StrengthSummary
from gameplay.services.virtual_player_core.random_context import RandomContext
from guests.services.salary import SalaryBatchQuote


def _context() -> RandomContext:
    return RandomContext(
        rng_version=1,
        growth_seed=271828,
        engine_version=2,
        plan_schema_version=1,
        policy_version=1,
        maintenance_sequence=7,
    )


def _intent(key: str, *, utility: float) -> DevelopmentIntent:
    strength = StrengthSummary(
        composite=100,
        components={"arena_lineup_power": 100},
    )
    return DevelopmentIntent(
        business_key=key,
        action_kind="training",
        source_prestige_band="newbie",
        target_prestige_band="newbie",
        strength_before=strength,
        strength_after=strength,
        utility_score=utility,
    )


def test_selector_skips_a_higher_utility_rejected_candidate() -> None:
    rejected = _intent("training:rejected", utility=10)
    allowed = _intent("training:allowed", utility=5)
    selected = select_candidate_assessment(
        ((rejected, allowed),),
        assessments=(
            CandidateAssessment(
                intent=rejected,
                rejection_reasons=("multi_band_transition",),
                retryable=True,
            ),
            CandidateAssessment(intent=allowed),
        ),
        context=_context(),
        optimization_bias=1,
    )

    assert selected is not None
    assert selected.intent is allowed
    assert selected.allowed is True


def test_selector_retains_the_primary_rejection_when_all_group_candidates_fail() -> None:
    first = _intent("training:first", utility=10)
    second = _intent("training:second", utility=5)
    selected = select_candidate_assessment(
        ((first, second),),
        assessments=(
            CandidateAssessment(intent=first, rejection_reasons=("multi_band_transition",)),
            CandidateAssessment(intent=second, rejection_reasons=("domain_constraint",)),
        ),
        context=_context(),
        optimization_bias=1,
    )

    assert selected is not None
    assert selected.intent is first
    assert selected.primary_rejection_reason == "multi_band_transition"


def test_selector_keeps_a_resource_blocked_priority_group_from_falling_back() -> None:
    blocked = _intent("building:blocked", utility=10)
    fallback = _intent("training:fallback", utility=1)
    selected = select_candidate_assessment(
        ((blocked,), (fallback,)),
        assessments=(
            CandidateAssessment(
                intent=blocked,
                rejection_reasons=("insufficient_resource",),
                retryable=True,
            ),
            CandidateAssessment(intent=fallback),
        ),
        context=_context(),
        optimization_bias=1,
        resource_barrier_group_indexes=frozenset({0}),
    )

    assert selected is not None
    assert selected.intent is blocked
    assert selected.allowed is False
    assert selected.primary_rejection_reason == "insufficient_resource"


def test_selector_does_not_promote_observation_only_rejections() -> None:
    disallowed = _intent("building:disallowed", utility=100)

    assert (
        select_candidate_assessment(
            (),
            assessments=(
                CandidateAssessment(
                    intent=disallowed,
                    rejection_reasons=("trigger_action_disallowed",),
                ),
            ),
            context=_context(),
            optimization_bias=1,
        )
        is None
    )


def test_candidate_assessment_exposes_event_power_projection_separately_from_generic_cap() -> None:
    intent = _intent("training:event-cap", utility=10)

    assessment = CandidateAssessment(
        intent=intent,
        projected_selected_power=125,
        event_power_cap=120,
        rejection_reasons=("event_power_cap",),
    )

    assert assessment.allowed is False
    assert assessment.primary_rejection_reason == "event_power_cap"
    assert assessment.summary_payload()["projected_selected_power"] == 125
    assert assessment.summary_payload()["event_power_cap"] == 120


def test_resource_snapshot_distinguishes_runway_from_physical_shortfall() -> None:
    current_salary = SalaryBatchQuote(
        for_date=date(2026, 8, 8),
        guest_ids=(1,),
        unpaid_guest_ids=(1,),
        total_amount=10,
    )
    next_salary = SalaryBatchQuote(
        for_date=date(2026, 8, 9),
        guest_ids=(1,),
        unpaid_guest_ids=(1,),
        total_amount=10,
    )
    snapshot = ResourcePlanningSnapshot(
        current_resources=(("grain", 50), ("silver", 110)),
        production_deltas=(),
        post_settlement_resources=(("grain", 50), ("silver", 110)),
        current_salary_quote=current_salary,
        current_salary_payable=True,
        post_salary_resources=(("grain", 50), ("silver", 100)),
        next_day_salary_quote=next_salary,
        operating_buffer=(("grain", 0), ("silver", 10)),
        protected_resources=(("grain", 0), ("silver", 20)),
        spendable_resources=(("grain", 50), ("silver", 80)),
    )

    _costs, _before, _after, runway_reasons = snapshot.assess_costs({"silver": 90})
    _costs, _before, _after, stock_reasons = snapshot.assess_costs({"grain": 60})

    assert runway_reasons == ("salary_runway_protected",)
    assert stock_reasons == ("insufficient_resource",)


def test_resource_snapshot_exposes_liquidity_forecast_and_affordability_time() -> None:
    current_salary = SalaryBatchQuote(
        for_date=date(2026, 8, 8),
        guest_ids=(1,),
        unpaid_guest_ids=(),
        total_amount=10,
    )
    next_salary = SalaryBatchQuote(
        for_date=date(2026, 8, 9),
        guest_ids=(1,),
        unpaid_guest_ids=(1,),
        total_amount=10,
    )
    snapshot = ResourcePlanningSnapshot(
        current_resources=(("grain", 50), ("silver", 110)),
        production_deltas=(),
        post_settlement_resources=(("grain", 50), ("silver", 110)),
        current_salary_quote=current_salary,
        current_salary_payable=True,
        post_salary_resources=(("grain", 50), ("silver", 100)),
        next_day_salary_quote=next_salary,
        operating_buffer=(("grain", 0), ("silver", 0)),
        protected_resources=(("grain", 0), ("silver", 0)),
        spendable_resources=(("grain", 50), ("silver", 100)),
        production_rates=(("grain", -5.0), ("silver", 20.0)),
        silver_forecast_24h=570,
        silver_forecast_72h=1_000,
        grain_forecast_24h=0,
        grain_forecast_72h=0,
    )
    now = datetime(2026, 8, 8, 12, tzinfo=UTC)

    assert snapshot.silver_liquidity_state == "healthy"
    affordable_at = snapshot.next_affordable_at({"silver": 200}, now=now)

    assert affordable_at is not None
    assert affordable_at > now
    assert snapshot.next_affordable_at({"grain": 60}, now=now) is None


def test_recurring_outflow_changes_forecast_without_reserving_current_silver() -> None:
    current_salary = SalaryBatchQuote(
        for_date=date(2026, 8, 8),
        guest_ids=(),
        unpaid_guest_ids=(),
        total_amount=0,
    )
    next_salary = SalaryBatchQuote(
        for_date=date(2026, 8, 9),
        guest_ids=(),
        unpaid_guest_ids=(),
        total_amount=0,
    )
    snapshot = ResourcePlanningSnapshot(
        current_resources=(("grain", 0), ("silver", 10_000)),
        production_deltas=(),
        post_settlement_resources=(("grain", 0), ("silver", 10_000)),
        current_salary_quote=current_salary,
        current_salary_payable=True,
        post_salary_resources=(("grain", 0), ("silver", 10_000)),
        next_day_salary_quote=next_salary,
        operating_buffer=(("grain", 0), ("silver", 0)),
        protected_resources=(("grain", 0), ("silver", 0)),
        spendable_resources=(("grain", 0), ("silver", 10_000)),
        production_rates=(("grain", 0.0), ("silver", 300.0)),
        silver_forecast_24h=11_200,
        silver_forecast_72h=13_600,
        recurring_silver_outflow_daily=6_000,
    )
    now = datetime(2026, 8, 8, 12, tzinfo=UTC)

    assert dict(snapshot.spendable_resources)["silver"] == 10_000
    assert snapshot.assess_costs({"silver": 9_000})[-1] == ()
    assert snapshot.next_affordable_at({"silver": 11_000}, now=now) is not None
