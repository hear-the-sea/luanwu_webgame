from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.db import transaction
from django.utils import timezone

from gameplay.models import (
    ArenaReserveTrainingAssignment,
    ArenaTournament,
    ArenaVirtualDemand,
    ArenaVirtualReserveMember,
    BotMaintenanceAttempt,
    BotMaintenanceExecution,
)
from gameplay.services.arena import virtual_reserve_fill, virtual_reserve_pool
from gameplay.services.virtual_player_core import maintenance
from gameplay.services.virtual_player_core.contracts import AcceleratedGrowthOutcome, MaintenanceTrigger
from gameplay.services.virtual_player_core.recovery import RecoveryFailureClass, record_recovery_failure
from tests.arena_services.test_virtual_reserve import _create_bot_profile, _create_tournament_demand

pytestmark = pytest.mark.django_db


def _reserved_training(*, finalize_busy=True):
    now = timezone.now()
    demand = _create_tournament_demand()
    profile = _create_bot_profile(f"terminal_assignment_{demand.id}", guest_stats=[(150, 150, 25)])
    member = ArenaVirtualReserveMember.objects.create(
        demand=demand,
        profile=profile,
        state=ArenaVirtualReserveMember.State.TRAINING,
        current_lineup_power=450,
        next_acceleration_at=now - timedelta(minutes=1),
    )
    claim = virtual_reserve_pool._claim_due_virtual_reserve_growth(
        member_id=member.id, demand_id=demand.id, now=now, growth_targets={}
    )
    assert claim is not None
    guest = profile.manor.guests.first()
    maintenance._persist_arena_training_assignment_from_plan(
        SimpleNamespace(
            trigger_policy=SimpleNamespace(trigger=MaintenanceTrigger.ARENA_ACCELERATION),
            action_kind="training",
            target_id=guest.id,
            manor_id=profile.manor_id,
        ),
        operation_id=claim.operation_id,
        member_id=member.id,
        round_ordinal=claim.round_ordinal,
        action_ordinal_in_round=claim.action_ordinal_in_round,
    )
    if finalize_busy:
        assert virtual_reserve_pool._finalize_virtual_reserve_growth(
            claim, growth_outcome=AcceleratedGrowthOutcome.BUSY, now=now
        )
    return member, claim, now


@transaction.atomic
def _complete_and_expire(member, now):
    virtual_reserve_fill._complete_demand_fill(demand=member.demand, profile_ids=[], now=now)
    assert ArenaVirtualReserveMember.objects.filter(pk=member.id).exists()
    return now + virtual_reserve_pool.EXHAUSTED_LEASE_GRACE + timedelta(seconds=1)


def test_completed_fill_reclaims_audited_busy_spare_and_unblocks_daily_ownership():
    member, claim, now = _reserved_training()
    assert BotMaintenanceAttempt.objects.filter(
        receipt_operation_id=claim.operation_id, outcome=BotMaintenanceAttempt.Outcome.BUSY
    ).exists()
    cleanup_at = _complete_and_expire(member, now)

    assert virtual_reserve_pool.release_expired_exhausted_virtual_reserve_members(now=cleanup_at, limit=1) == 1
    assert not ArenaVirtualReserveMember.objects.filter(profile_id=member.profile_id).exists()
    assert not ArenaReserveTrainingAssignment.objects.filter(member_id=member.id).exists()


@pytest.mark.parametrize("fence", ["receipt", "recovery", "uncertain_attempt", "no_completed_audit"])
def test_terminal_assignment_cleanup_preserves_commit_fences_and_unfinished_work(fence):
    member, claim, now = _reserved_training()
    if fence == "receipt":
        BotMaintenanceExecution.objects.create(
            operation_id=claim.operation_id,
            profile_id=member.profile_id,
            attempt_ordinal=1,
            trigger=BotMaintenanceExecution.Trigger.ARENA_ACCELERATION,
            outcome=BotMaintenanceExecution.Outcome.APPLIED,
            schedule_disposition=BotMaintenanceExecution.ScheduleDisposition.PRESERVE_NORMAL_SCHEDULE,
            maintenance_sequence_before=0,
            maintenance_sequence_after=1,
            next_growth_at_before=now,
            next_growth_at_after=now,
            request_digest="a" * 64,
            requested_at=now,
        )
    elif fence == "recovery":
        record_recovery_failure(
            scope="arena_member",
            entity_key=f"member:{member.id}",
            failure_code=RecoveryFailureClass.COMMIT_UNCERTAIN,
            operation_id=claim.operation_id,
            now=now,
        )
    elif fence == "uncertain_attempt":
        BotMaintenanceAttempt.objects.create(
            operation_id=f"uncertain-{member.id}",
            profile_id=member.profile_id,
            trigger=MaintenanceTrigger.ARENA_ACCELERATION.value,
            attempt_ordinal=1,
            outcome=BotMaintenanceAttempt.Outcome.COMMIT_UNCERTAIN,
            receipt_operation_id=claim.operation_id,
            started_at=now,
        )
    else:
        BotMaintenanceAttempt.objects.filter(receipt_operation_id=claim.operation_id).delete()
    cleanup_at = _complete_and_expire(member, now)

    assert virtual_reserve_pool.release_expired_exhausted_virtual_reserve_members(now=cleanup_at, limit=1) == 0
    assert ArenaVirtualReserveMember.objects.filter(pk=member.id).exists()
    assert ArenaReserveTrainingAssignment.objects.filter(member_id=member.id).exists()


def test_terminal_assignment_cleanup_does_not_release_active_claim():
    member, _claim, now = _reserved_training(finalize_busy=False)
    cleanup_at = _complete_and_expire(member, now)

    assert virtual_reserve_pool.release_expired_exhausted_virtual_reserve_members(now=cleanup_at, limit=1) == 0
    assert ArenaVirtualReserveMember.objects.filter(pk=member.id).exists()


def test_protected_old_assignment_does_not_starve_a_completed_busy_spare():
    protected, protected_claim, now = _reserved_training()
    BotMaintenanceAttempt.objects.filter(receipt_operation_id=protected_claim.operation_id).delete()
    _complete_and_expire(protected, now)
    ArenaTournament.objects.filter(pk=protected.demand.tournament_id).update(status=ArenaTournament.Status.COMPLETED)
    releasable, _claim, later = _reserved_training()
    cleanup_at = _complete_and_expire(releasable, later)

    assert virtual_reserve_pool.release_expired_exhausted_virtual_reserve_members(now=cleanup_at, limit=1) == 1
    assert ArenaVirtualReserveMember.objects.filter(pk=protected.id).exists()
    assert not ArenaVirtualReserveMember.objects.filter(pk=releasable.id).exists()
    assert ArenaVirtualDemand.objects.get(pk=releasable.demand_id).status == ArenaVirtualDemand.Status.SATISFIED
