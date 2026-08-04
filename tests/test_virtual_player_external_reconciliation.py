from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from django.db import DatabaseError, transaction
from django.test import override_settings
from django.utils import timezone

from gameplay.models import (
    BotExternalStrengthReconciliation,
    BotPopulationRecomputeDemand,
    BotProfile,
    BotVirtualPlayerHealth,
)
from gameplay.services.arena import virtual_reserve_fill, virtual_reserve_pool
from gameplay.services.manor.core import ensure_manor
from gameplay.services.virtual_player_core import external_reconciliation
from gameplay.services.virtual_player_core.external_reconciliation import (
    ExternalReconciliationRetryableError,
    capture_external_reconciliation_anchors,
    claim_external_reconciliation,
    create_external_reconciliation_intent,
    reconcile_claimed_external_reconciliation,
    reconcile_external_reconciliation,
)
from gameplay.services.virtual_player_core.projection import StrengthSummary
from gameplay.services.virtual_player_core.selectors import without_unresolved_external_reconciliations
from gameplay.tasks import virtual_players as virtual_player_tasks

pytestmark = pytest.mark.django_db


def _create_profile(
    django_user_model,
    *,
    username: str,
    prestige: int = 100,
    current_band: str = "newbie",
) -> BotProfile:
    now = timezone.now()
    manor = ensure_manor(django_user_model.objects.create_user(username=username, password="pass123"))
    manor.region = "north"
    manor.prestige = prestige
    manor.save(update_fields=["region", "prestige"])
    return BotProfile.objects.create(
        manor=manor,
        archetype=BotProfile.Archetype.BALANCED,
        state=BotProfile.State.ACTIVE,
        prestige_band="newbie",
        target_prestige_band="newbie",
        current_prestige_band=current_band,
        growth_seed=981_001,
        next_growth_at=now + timedelta(hours=1),
        abandon_at=now + timedelta(days=30),
        retire_at=now + timedelta(days=60),
        engine_version=2,
        rng_version=1,
        plan_schema_version=1,
        policy_version=1,
        policy_checksum="a" * 64,
        development_profile={},
        last_strength_increase_at=now - timedelta(days=1),
        v2_enrolled_at=now - timedelta(days=1),
    )


def _capture(profile: BotProfile):
    return capture_external_reconciliation_anchors([profile.manor_id])[profile.manor_id]


def _create_intent(
    profile: BotProfile,
    *,
    event_id: str,
    origin_committed_at=None,
    anchor=None,
) -> BotExternalStrengthReconciliation:
    committed_at = origin_committed_at or timezone.now()
    resolved_anchor = anchor or _capture(profile)
    with transaction.atomic():
        result = create_external_reconciliation_intent(
            anchor=resolved_anchor,
            domain_event_kind="test_external_result",
            domain_event_id=event_id,
            origin_committed_at=committed_at,
        )
    return BotExternalStrengthReconciliation.objects.get(pk=result.reconciliation_id)


def test_external_reconciliation_tasks_are_transport_only(monkeypatch) -> None:
    worker_result = external_reconciliation.ExternalReconciliationProcessResult(
        reconciliation_id=41,
        profile_id=9,
        status=BotExternalStrengthReconciliation.Status.APPLIED,
    )
    scanner_results = (
        worker_result,
        external_reconciliation.ExternalReconciliationProcessResult(
            reconciliation_id=42,
            profile_id=10,
            status=external_reconciliation.NO_WORK_STATUS,
        ),
    )
    worker_calls: list[int] = []
    scanner_calls: list[int] = []

    monkeypatch.setattr(
        virtual_player_tasks,
        "reconcile_external_reconciliation",
        lambda reconciliation_id: (worker_calls.append(reconciliation_id) or worker_result),
    )
    monkeypatch.setattr(
        virtual_player_tasks,
        "scan_external_reconciliations",
        lambda *, limit: scanner_calls.append(limit) or scanner_results,
    )

    assert virtual_player_tasks.reconcile_external_strength_reconciliation_task.run(41) == worker_result.to_payload()
    assert virtual_player_tasks.scan_external_strength_reconciliations_task.run(limit=17) == [
        result.to_payload() for result in scanner_results
    ]
    assert worker_calls == [41]
    assert scanner_calls == [17]


@pytest.mark.django_db(transaction=True)
def test_intent_producer_requires_domain_transaction_and_is_strictly_idempotent(
    django_user_model,
    monkeypatch,
) -> None:
    profile = _create_profile(
        django_user_model,
        username="external_reconciliation_idempotency",
    )
    anchor = _capture(profile)
    committed_at = timezone.now()
    monkeypatch.setattr(
        external_reconciliation,
        "_queue_external_reconciliation",
        lambda reconciliation_id: True,
    )

    with pytest.raises(
        external_reconciliation.ExternalReconciliationError,
        match="inside transaction.atomic",
    ):
        create_external_reconciliation_intent(
            anchor=anchor,
            domain_event_kind="raid_result",
            domain_event_id="raid-41:attacker",
            origin_committed_at=committed_at,
        )

    with transaction.atomic():
        first = create_external_reconciliation_intent(
            anchor=anchor,
            domain_event_kind="raid_result",
            domain_event_id="raid-41:attacker",
            origin_committed_at=committed_at,
        )
        second = create_external_reconciliation_intent(
            anchor=anchor,
            domain_event_kind="raid_result",
            domain_event_id="raid-41:attacker",
            origin_committed_at=committed_at,
        )

    assert first.created is True
    assert second.created is False
    assert first.reconciliation_id == second.reconciliation_id
    assert BotExternalStrengthReconciliation.objects.count() == 1

    conflicting_summary = StrengthSummary(
        composite=anchor.pre_strength_summary.composite + 1,
        components=dict(anchor.pre_strength_summary.components),
    )
    with (
        transaction.atomic(),
        pytest.raises(
            external_reconciliation.ExternalReconciliationConflict,
            match="different immutable payload",
        ),
    ):
        create_external_reconciliation_intent(
            anchor=replace(anchor, pre_strength_summary=conflicting_summary),
            domain_event_kind="raid_result",
            domain_event_id="raid-41:attacker",
            origin_committed_at=committed_at,
        )


def test_intent_is_rolled_back_with_the_originating_domain_transaction(
    django_user_model,
) -> None:
    profile = _create_profile(
        django_user_model,
        username="external_reconciliation_origin_rollback",
    )
    anchor = _capture(profile)

    with pytest.raises(RuntimeError, match="force domain rollback"):
        with transaction.atomic():
            create_external_reconciliation_intent(
                anchor=anchor,
                domain_event_kind="raid_result",
                domain_event_id="raid-42:defender",
                origin_committed_at=timezone.now(),
            )
            raise RuntimeError("force domain rollback")

    assert not BotExternalStrengthReconciliation.objects.exists()


def test_profile_and_population_phases_commit_cross_band_result_exactly_once(
    django_user_model,
) -> None:
    profile = _create_profile(
        django_user_model,
        username="external_reconciliation_cross_band",
        prestige=499,
    )
    anchor = _capture(profile)
    committed_at = timezone.now()
    profile.manor.prestige = 500
    profile.manor.save(update_fields=["prestige"])
    intent = _create_intent(
        profile,
        event_id="cross-band",
        origin_committed_at=committed_at,
        anchor=anchor,
    )

    profile_result = reconcile_external_reconciliation(
        intent.id,
        now=committed_at + timedelta(seconds=1),
    )
    intent.refresh_from_db()
    profile.refresh_from_db()

    assert profile_result.status == (BotExternalStrengthReconciliation.Status.PENDING_POPULATION)
    assert intent.status == BotExternalStrengthReconciliation.Status.PENDING_POPULATION
    assert intent.profile_attempt_count == 1
    assert intent.population_attempt_count == 0
    assert intent.profile_completed_at == committed_at + timedelta(seconds=1)
    assert intent.applied_at is None
    assert profile.current_prestige_band == "junior"
    assert profile.last_strength_increase_at == committed_at
    assert intent.result_summary["strength_increased"] is True
    assert intent.result_summary["population_handoff_required"] is True
    assert not BotPopulationRecomputeDemand.objects.exists()

    population_result = reconcile_external_reconciliation(
        intent.id,
        now=committed_at + timedelta(seconds=2),
    )
    intent.refresh_from_db()

    assert population_result.status == BotExternalStrengthReconciliation.Status.APPLIED
    assert intent.status == BotExternalStrengthReconciliation.Status.APPLIED
    assert intent.population_attempt_count == 1
    assert intent.population_handoff_completed_at == committed_at + timedelta(seconds=2)
    assert intent.applied_at == committed_at + timedelta(seconds=2)
    assert list(
        BotPopulationRecomputeDemand.objects.order_by("prestige_band").values_list(
            "region",
            "prestige_band",
            "requested_revision",
        )
    ) == [
        ("north", "junior", 1),
        ("north", "newbie", 1),
    ]

    repeated = reconcile_external_reconciliation(
        intent.id,
        now=committed_at + timedelta(seconds=3),
    )
    assert repeated.status == external_reconciliation.NO_WORK_STATUS
    assert list(BotPopulationRecomputeDemand.objects.values_list("prestige_band", "requested_revision")) == [
        ("newbie", 1),
        ("junior", 1),
    ]


def test_same_band_profile_phase_applies_without_population_handoff(
    django_user_model,
) -> None:
    profile = _create_profile(
        django_user_model,
        username="external_reconciliation_same_band",
        prestige=100,
    )
    anchor = _capture(profile)
    committed_at = timezone.now()
    profile.manor.prestige = 101
    profile.manor.save(update_fields=["prestige"])
    intent = _create_intent(
        profile,
        event_id="same-band",
        origin_committed_at=committed_at,
        anchor=anchor,
    )

    result = reconcile_external_reconciliation(
        intent.id,
        now=committed_at + timedelta(seconds=1),
    )
    intent.refresh_from_db()

    assert result.status == BotExternalStrengthReconciliation.Status.APPLIED
    assert intent.profile_completed_at == committed_at + timedelta(seconds=1)
    assert intent.population_handoff_completed_at is None
    assert intent.applied_at == committed_at + timedelta(seconds=1)
    assert intent.result_summary["population_handoff_required"] is False
    assert not BotPopulationRecomputeDemand.objects.exists()


def test_later_intent_cannot_pass_an_unresolved_earlier_intent(
    django_user_model,
) -> None:
    profile = _create_profile(
        django_user_model,
        username="external_reconciliation_ordering",
    )
    start = timezone.now()
    earlier = _create_intent(
        profile,
        event_id="earlier",
        origin_committed_at=start,
    )
    later = _create_intent(
        profile,
        event_id="later",
        origin_committed_at=start + timedelta(seconds=1),
    )

    assert (
        claim_external_reconciliation(
            later.id,
            now=start + timedelta(seconds=2),
        )
        is None
    )
    earlier_claim = claim_external_reconciliation(
        earlier.id,
        now=start + timedelta(seconds=2),
    )
    assert earlier_claim is not None
    assert (
        reconcile_claimed_external_reconciliation(
            earlier_claim,
            now=start + timedelta(seconds=3),
        ).status
        == BotExternalStrengthReconciliation.Status.APPLIED
    )
    assert (
        claim_external_reconciliation(
            later.id,
            now=start + timedelta(seconds=4),
        )
        is not None
    )


def test_expired_claim_is_reclaimed_and_stale_token_cannot_finalize(
    django_user_model,
) -> None:
    profile = _create_profile(
        django_user_model,
        username="external_reconciliation_fencing",
    )
    start = timezone.now()
    intent = _create_intent(
        profile,
        event_id="fencing",
        origin_committed_at=start,
    )
    old_claim = claim_external_reconciliation(intent.id, now=start)
    assert old_claim is not None

    reclaimed_at = start + timedelta(minutes=5, seconds=1)
    new_claim = claim_external_reconciliation(intent.id, now=reclaimed_at)
    assert new_claim is not None
    assert new_claim.claim_token != old_claim.claim_token

    stale_result = reconcile_claimed_external_reconciliation(
        old_claim,
        now=reclaimed_at + timedelta(seconds=1),
    )
    assert stale_result.status == external_reconciliation.CLAIM_LOST_STATUS

    current_result = reconcile_claimed_external_reconciliation(
        new_claim,
        now=reclaimed_at + timedelta(seconds=2),
    )
    assert current_result.status == BotExternalStrengthReconciliation.Status.APPLIED
    intent.refresh_from_db()
    assert intent.profile_attempt_count == 2


@override_settings(VIRTUAL_PLAYER_HEALTH_FAILURE_THRESHOLD=100)
def test_retryable_failure_resets_attempt_budget_instead_of_quarantining(
    django_user_model,
    monkeypatch,
) -> None:
    profile = _create_profile(
        django_user_model,
        username="external_reconciliation_retry",
    )
    start = timezone.now()
    intent = _create_intent(
        profile,
        event_id="retry",
        origin_committed_at=start,
    )

    def fail_profile_phase(*args, **kwargs):
        raise ExternalReconciliationRetryableError(
            "profile_dependency_unavailable",
            "temporary dependency failure",
        )

    monkeypatch.setattr(
        external_reconciliation,
        "_apply_claimed_profile_phase",
        fail_profile_phase,
    )

    attempt_at = start
    for expected_attempt in range(1, 13):
        result = reconcile_external_reconciliation(intent.id, now=attempt_at)
        intent.refresh_from_db()
        if expected_attempt < 12:
            assert intent.profile_attempt_count == expected_attempt
            expected_backoff = min(21_600, 60 * (2 ** (expected_attempt - 1)))
            assert result.status == BotExternalStrengthReconciliation.Status.PENDING_PROFILE
            assert intent.available_at == attempt_at + timedelta(seconds=expected_backoff)
            attempt_at = intent.available_at
        else:
            assert result.status == BotExternalStrengthReconciliation.Status.PENDING_PROFILE
            assert intent.profile_attempt_count == 0

    assert intent.quarantined_phase == ""
    assert intent.failure_code == "profile_dependency_unavailable"
    assert len(intent.last_error_digest) == 64
    assert "temporary dependency failure" not in str(intent.result_summary)


def test_retryable_dependency_opens_health_circuit_without_consuming_more_attempts(
    django_user_model,
    monkeypatch,
) -> None:
    profile = _create_profile(
        django_user_model,
        username="external_reconciliation_health_circuit",
    )
    start = timezone.now()
    intent = _create_intent(
        profile,
        event_id="health-circuit",
        origin_committed_at=start,
    )

    def fail_profile_phase(*args, **kwargs):
        raise ExternalReconciliationRetryableError(
            "profile_dependency_unavailable",
            "temporary dependency failure",
        )

    monkeypatch.setattr(
        external_reconciliation,
        "_apply_claimed_profile_phase",
        fail_profile_phase,
    )

    attempt_at = start
    for _expected_attempt in range(1, 4):
        reconcile_external_reconciliation(intent.id, now=attempt_at)
        intent.refresh_from_db()
        attempt_at = intent.available_at

    health = BotVirtualPlayerHealth.objects.get(key=BotVirtualPlayerHealth.GLOBAL_KEY)
    assert health.status == BotVirtualPlayerHealth.Status.DEGRADED
    assert health.next_probe_at is not None
    assert intent.profile_attempt_count == 3

    reconcile_external_reconciliation(intent.id, now=attempt_at)
    intent.refresh_from_db()
    health.refresh_from_db()
    assert intent.profile_attempt_count == 3
    assert intent.status == BotExternalStrengthReconciliation.Status.PENDING_PROFILE
    assert intent.available_at == health.next_probe_at


def test_database_failure_is_requeued_as_retryable_instead_of_reraised(
    django_user_model,
    monkeypatch,
) -> None:
    profile = _create_profile(
        django_user_model,
        username="external_reconciliation_database_failure",
    )
    start = timezone.now()
    intent = _create_intent(
        profile,
        event_id="database-failure",
        origin_committed_at=start,
    )

    def fail_profile_phase(*args, **kwargs):
        raise DatabaseError("database temporarily unavailable")

    monkeypatch.setattr(
        external_reconciliation,
        "_apply_claimed_profile_phase",
        fail_profile_phase,
    )

    result = reconcile_external_reconciliation(intent.id, now=start)

    intent.refresh_from_db()
    assert result.status == BotExternalStrengthReconciliation.Status.PENDING_PROFILE
    assert intent.status == BotExternalStrengthReconciliation.Status.PENDING_PROFILE
    assert intent.failure_code == "infrastructure_unavailable"
    assert intent.quarantined_at is None
    assert intent.profile_attempt_count == 1


@override_settings(VIRTUAL_PLAYER_HEALTH_FAILURE_THRESHOLD=100)
def test_repeated_expired_claims_reset_budget_without_quarantine(
    django_user_model,
) -> None:
    profile = _create_profile(
        django_user_model,
        username="external_reconciliation_expired_claims",
    )
    start = timezone.now()
    intent = _create_intent(
        profile,
        event_id="expired-claims",
        origin_committed_at=start,
    )

    claim = claim_external_reconciliation(intent.id, now=start)
    assert claim is not None
    for _index in range(12):
        claim = claim_external_reconciliation(
            intent.id,
            now=claim.claim_expires_at + timedelta(seconds=1),
        )
        if claim is None:
            break

    intent.refresh_from_db()
    assert claim is None
    assert intent.status == BotExternalStrengthReconciliation.Status.PENDING_PROFILE
    assert intent.profile_attempt_count == 0
    assert intent.quarantined_at is None


def test_exhausted_retryable_intent_is_requeued_without_quarantine(
    django_user_model,
) -> None:
    profile = _create_profile(
        django_user_model,
        username="external_reconciliation_stale_budget",
    )
    start = timezone.now()
    intent = _create_intent(
        profile,
        event_id="stale-budget",
        origin_committed_at=start,
    )
    intent.profile_attempt_count = 12
    intent.failure_code = "profile_dependency_unavailable"
    intent.available_at = start
    intent.save(update_fields=["profile_attempt_count", "failure_code", "available_at", "updated_at"])

    assert claim_external_reconciliation(intent.id, now=start) is None

    intent.refresh_from_db()
    assert intent.status == BotExternalStrengthReconciliation.Status.PENDING_PROFILE
    assert intent.profile_attempt_count == 0
    assert intent.quarantined_at is None
    assert intent.available_at >= start + timedelta(hours=6)


def test_permanent_payload_error_is_quarantined_without_retry(
    django_user_model,
) -> None:
    profile = _create_profile(
        django_user_model,
        username="external_reconciliation_bad_payload",
    )
    start = timezone.now()
    intent = BotExternalStrengthReconciliation.objects.create(
        profile_id=profile.id,
        domain_event_kind="bad_payload",
        domain_event_id="bad-payload",
        origin_committed_at=start,
        pre_strength_summary={"score": 10},
        pre_prestige_band="newbie",
        available_at=start,
    )

    result = reconcile_external_reconciliation(intent.id, now=start)
    intent.refresh_from_db()

    assert result.status == BotExternalStrengthReconciliation.Status.QUARANTINED
    assert intent.profile_attempt_count == 1
    assert intent.quarantined_phase == BotExternalStrengthReconciliation.Phase.PROFILE
    assert intent.failure_code == "invalid_strength_summary"


def test_unexpected_error_is_reraised_and_claim_recovers_only_after_lease_expiry(
    django_user_model,
    monkeypatch,
) -> None:
    profile = _create_profile(
        django_user_model,
        username="external_reconciliation_unexpected",
    )
    start = timezone.now()
    intent = _create_intent(
        profile,
        event_id="unexpected",
        origin_committed_at=start,
    )

    def raise_unexpected(*args, **kwargs):
        raise RuntimeError("unexpected programmer error")

    monkeypatch.setattr(
        external_reconciliation,
        "_apply_claimed_profile_phase",
        raise_unexpected,
    )

    with pytest.raises(RuntimeError, match="unexpected programmer error"):
        reconcile_external_reconciliation(intent.id, now=start)

    intent.refresh_from_db()
    original_token = intent.claim_token
    assert intent.status == BotExternalStrengthReconciliation.Status.CLAIMED_PROFILE
    assert original_token is not None
    assert (
        claim_external_reconciliation(
            intent.id,
            now=start + timedelta(minutes=4),
        )
        is None
    )
    reclaimed = claim_external_reconciliation(
        intent.id,
        now=start + timedelta(minutes=5, seconds=1),
    )
    assert reclaimed is not None
    assert reclaimed.claim_token != original_token


def test_population_phase_rolls_back_demands_when_finalize_path_raises(
    django_user_model,
    monkeypatch,
) -> None:
    profile = _create_profile(
        django_user_model,
        username="external_reconciliation_population_rollback",
        prestige=499,
    )
    anchor = _capture(profile)
    start = timezone.now()
    profile.manor.prestige = 500
    profile.manor.save(update_fields=["prestige"])
    intent = _create_intent(
        profile,
        event_id="population-rollback",
        origin_committed_at=start,
        anchor=anchor,
    )
    assert (
        reconcile_external_reconciliation(
            intent.id,
            now=start + timedelta(seconds=1),
        ).status
        == BotExternalStrengthReconciliation.Status.PENDING_POPULATION
    )

    original_merge = external_reconciliation.population_runtime.merge_population_recompute_demands

    def merge_then_fail(cells, *, now=None):
        original_merge(cells, now=now)
        raise RuntimeError("population finalize failed")

    monkeypatch.setattr(
        external_reconciliation.population_runtime,
        "merge_population_recompute_demands",
        merge_then_fail,
    )

    with pytest.raises(RuntimeError, match="population finalize failed"):
        reconcile_external_reconciliation(
            intent.id,
            now=start + timedelta(seconds=2),
        )

    intent.refresh_from_db()
    assert intent.status == BotExternalStrengthReconciliation.Status.CLAIMED_POPULATION
    assert not BotPopulationRecomputeDemand.objects.exists()


def test_unresolved_and_quarantined_intents_are_excluded_from_arena_candidates(
    django_user_model,
) -> None:
    profile = _create_profile(
        django_user_model,
        username="external_reconciliation_arena_exclusion",
    )
    intent = _create_intent(profile, event_id="arena-exclusion")

    assert list(without_unresolved_external_reconciliations(BotProfile.objects.filter(pk=profile.id))) == []
    assert list(virtual_reserve_fill._candidates([], profile_ids=[profile.id])) == []
    assert list(virtual_reserve_pool._candidate_queryset([BotProfile.State.ACTIVE]).filter(pk=profile.id)) == []

    intent.status = BotExternalStrengthReconciliation.Status.QUARANTINED
    intent.quarantined_at = timezone.now()
    intent.quarantined_phase = BotExternalStrengthReconciliation.Phase.PROFILE
    intent.failure_code = "manual_review_required"
    intent.save(
        update_fields=[
            "status",
            "quarantined_at",
            "quarantined_phase",
            "failure_code",
            "updated_at",
        ]
    )
    assert list(without_unresolved_external_reconciliations(BotProfile.objects.filter(pk=profile.id))) == []

    intent.status = BotExternalStrengthReconciliation.Status.APPLIED
    intent.quarantined_at = None
    intent.quarantined_phase = ""
    intent.failure_code = ""
    intent.applied_at = timezone.now()
    intent.save(
        update_fields=[
            "status",
            "quarantined_at",
            "quarantined_phase",
            "failure_code",
            "applied_at",
            "updated_at",
        ]
    )
    assert list(without_unresolved_external_reconciliations(BotProfile.objects.filter(pk=profile.id))) == [profile]
