from __future__ import annotations

import threading
from datetime import timedelta

import pytest
from django.db import close_old_connections, connection, transaction
from django.utils import timezone

from gameplay.models import BotExternalStrengthReconciliation, BotProfile
from gameplay.services.manor.core import ensure_manor
from gameplay.services.virtual_player_core import external_reconciliation
from gameplay.services.virtual_player_core.external_reconciliation import (
    ExternalReconciliationClaim,
    capture_external_reconciliation_anchors,
    claim_external_reconciliation,
    claim_next_external_reconciliation,
    create_external_reconciliation_intent,
    reconcile_claimed_external_reconciliation,
)

pytestmark = [pytest.mark.integration]


def _require_mysql() -> None:
    if connection.vendor != "mysql":
        pytest.skip("external reconciliation concurrency evidence requires MySQL")


def _create_profile(django_user_model, *, username: str) -> BotProfile:
    now = timezone.now()
    manor = ensure_manor(django_user_model.objects.create_user(username=username, password="pass123"))
    manor.region = "north"
    manor.prestige = 100
    manor.save(update_fields=["region", "prestige"])
    return BotProfile.objects.create(
        manor=manor,
        archetype=BotProfile.Archetype.BALANCED,
        state=BotProfile.State.ACTIVE,
        prestige_band="newbie",
        target_prestige_band="newbie",
        current_prestige_band="newbie",
        growth_seed=982_001,
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


def _create_intent(
    profile: BotProfile,
    *,
    event_id: str,
    origin_committed_at,
) -> BotExternalStrengthReconciliation:
    anchor = capture_external_reconciliation_anchors([profile.manor_id])[profile.manor_id]
    with transaction.atomic():
        result = create_external_reconciliation_intent(
            anchor=anchor,
            domain_event_kind="mysql_concurrency_test",
            domain_event_id=event_id,
            origin_committed_at=origin_committed_at,
        )
    return BotExternalStrengthReconciliation.objects.get(pk=result.reconciliation_id)


@pytest.fixture(autouse=True)
def _disable_async_dispatch(monkeypatch) -> None:
    monkeypatch.setattr(
        external_reconciliation,
        "_queue_external_reconciliation",
        lambda reconciliation_id: True,
    )


@pytest.mark.django_db(transaction=True)
def test_concurrent_claims_for_same_intent_issue_exactly_one_token(
    django_user_model,
) -> None:
    _require_mysql()
    profile = _create_profile(
        django_user_model,
        username="external_reconciliation_concurrent_claim",
    )
    base_time = timezone.now() - timedelta(seconds=10)
    intent = _create_intent(
        profile,
        event_id="same-intent",
        origin_committed_at=base_time,
    )
    claim_at = timezone.now() + timedelta(seconds=1)
    start = threading.Barrier(2)
    claims: list[ExternalReconciliationClaim] = []
    errors: list[BaseException] = []
    results_guard = threading.Lock()

    def _worker() -> None:
        close_old_connections()
        try:
            start.wait(timeout=10)
            claim = claim_external_reconciliation(intent.id, now=claim_at)
            if claim is not None:
                with results_guard:
                    claims.append(claim)
        except BaseException as exc:  # pragma: no cover - asserted below
            with results_guard:
                errors.append(exc)
        finally:
            close_old_connections()

    threads = [threading.Thread(target=_worker, daemon=True) for _index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    intent.refresh_from_db()
    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert len(claims) == 1
    assert intent.status == BotExternalStrengthReconciliation.Status.CLAIMED_PROFILE
    assert intent.profile_attempt_count == 1
    assert intent.claim_token == claims[0].claim_token


@pytest.mark.django_db(transaction=True)
def test_expired_claim_reclaims_with_new_token_and_fences_stale_finalize(
    django_user_model,
) -> None:
    _require_mysql()
    profile = _create_profile(
        django_user_model,
        username="external_reconciliation_mysql_fencing",
    )
    base_time = timezone.now() - timedelta(seconds=10)
    intent = _create_intent(
        profile,
        event_id="lease-fencing",
        origin_committed_at=base_time,
    )
    first_claim_at = timezone.now() + timedelta(seconds=1)
    old_claim = claim_external_reconciliation(intent.id, now=first_claim_at)
    assert old_claim is not None

    reclaimed_at = first_claim_at + timedelta(minutes=5, seconds=1)
    new_claim = claim_external_reconciliation(intent.id, now=reclaimed_at)
    assert new_claim is not None
    assert new_claim.claim_token != old_claim.claim_token

    stale_result = reconcile_claimed_external_reconciliation(
        old_claim,
        now=reclaimed_at + timedelta(seconds=1),
    )
    intent.refresh_from_db()

    assert stale_result.status == external_reconciliation.CLAIM_LOST_STATUS
    assert intent.status == BotExternalStrengthReconciliation.Status.CLAIMED_PROFILE
    assert intent.profile_attempt_count == 2
    assert intent.claim_token == new_claim.claim_token
    assert intent.profile_completed_at is None

    current_result = reconcile_claimed_external_reconciliation(
        new_claim,
        now=reclaimed_at + timedelta(seconds=2),
    )
    intent.refresh_from_db()
    assert current_result.status == BotExternalStrengthReconciliation.Status.APPLIED
    assert intent.status == BotExternalStrengthReconciliation.Status.APPLIED


@pytest.mark.django_db(transaction=True)
def test_later_same_profile_intent_cannot_pass_unresolved_earlier_intent(
    django_user_model,
) -> None:
    _require_mysql()
    profile = _create_profile(
        django_user_model,
        username="external_reconciliation_mysql_ordering",
    )
    base_time = timezone.now() - timedelta(seconds=10)
    earlier = _create_intent(
        profile,
        event_id="earlier",
        origin_committed_at=base_time,
    )
    later = _create_intent(
        profile,
        event_id="later",
        origin_committed_at=base_time + timedelta(seconds=1),
    )
    claim_at = timezone.now() + timedelta(seconds=1)

    assert claim_external_reconciliation(later.id, now=claim_at) is None
    earlier_claim = claim_external_reconciliation(earlier.id, now=claim_at)
    assert earlier_claim is not None
    assert (
        reconcile_claimed_external_reconciliation(
            earlier_claim,
            now=claim_at + timedelta(seconds=1),
        ).status
        == BotExternalStrengthReconciliation.Status.APPLIED
    )
    assert (
        claim_external_reconciliation(
            later.id,
            now=claim_at + timedelta(seconds=2),
        )
        is not None
    )


@pytest.mark.django_db(transaction=True)
def test_claim_next_skips_locked_row_without_bypassing_same_profile_order(
    django_user_model,
) -> None:
    _require_mysql()
    ordered_profile = _create_profile(
        django_user_model,
        username="external_reconciliation_mysql_skip_locked_ordered",
    )
    independent_profile = _create_profile(
        django_user_model,
        username="external_reconciliation_mysql_skip_locked_independent",
    )
    base_time = timezone.now() - timedelta(seconds=10)
    earlier = _create_intent(
        ordered_profile,
        event_id="locked-earlier",
        origin_committed_at=base_time,
    )
    later = _create_intent(
        ordered_profile,
        event_id="blocked-later",
        origin_committed_at=base_time + timedelta(seconds=1),
    )
    independent = _create_intent(
        independent_profile,
        event_id="independent",
        origin_committed_at=base_time + timedelta(seconds=2),
    )
    lock_held = threading.Event()
    release_lock = threading.Event()
    holder_errors: list[BaseException] = []

    def _hold_earlier_row_lock() -> None:
        close_old_connections()
        try:
            with transaction.atomic():
                BotExternalStrengthReconciliation.objects.select_for_update().get(pk=earlier.id)
                lock_held.set()
                assert release_lock.wait(timeout=20)
        except BaseException as exc:  # pragma: no cover - asserted below
            holder_errors.append(exc)
        finally:
            close_old_connections()

    holder = threading.Thread(target=_hold_earlier_row_lock, daemon=True)
    holder.start()
    claim = None
    try:
        assert lock_held.wait(timeout=10)
        claim = claim_next_external_reconciliation(now=timezone.now() + timedelta(seconds=1))
    finally:
        release_lock.set()
        holder.join(timeout=30)

    later.refresh_from_db()
    independent.refresh_from_db()
    assert not holder.is_alive()
    assert holder_errors == []
    assert claim is not None
    assert claim.reconciliation_id == independent.id
    assert claim.profile_id == independent_profile.id
    assert later.status == BotExternalStrengthReconciliation.Status.PENDING_PROFILE
    assert independent.status == (BotExternalStrengthReconciliation.Status.CLAIMED_PROFILE)
