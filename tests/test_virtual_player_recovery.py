from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from gameplay.models import BotMaintenanceRecovery, BotPopulationRecomputeDemand
from gameplay.services.virtual_player_core.recovery import (
    RecoveryFailureClass,
    RecoveryPolicy,
    clear_recovery_failure,
    record_recovery_failure,
    recovery_circuit_is_open,
    requeue_recovery,
)


@pytest.mark.django_db
def test_recovery_episode_quarantines_and_formal_requeue_resets_backoff() -> None:
    now = timezone.now()
    policy = RecoveryPolicy(quarantine_after_failures=3, retry_base_seconds=10, retry_max_seconds=60)

    for _attempt in range(3):
        row = record_recovery_failure(
            scope=BotMaintenanceRecovery.Scope.PROFILE,
            entity_key="profile:42",
            failure_code=RecoveryFailureClass.PROGRAMMER_ERROR,
            error=RuntimeError("same failure"),
            now=now,
            policy=policy,
        )

    assert row.status == BotMaintenanceRecovery.Status.QUARANTINED
    assert row.failure_streak == 3
    assert row.quarantined_at == now
    assert row.next_retry_at is None

    requeued = requeue_recovery(
        scope=BotMaintenanceRecovery.Scope.PROFILE,
        entity_key="profile:42",
        now=now + timedelta(minutes=1),
        reason="repair_complete",
    )
    assert requeued.status == BotMaintenanceRecovery.Status.REQUEUED
    assert requeued.failure_streak == 0
    assert requeued.next_retry_at == now + timedelta(minutes=1)
    assert requeued.last_success_at is None
    assert requeued.payload["requeue_reason"] == "repair_complete"

    assert clear_recovery_failure(
        scope=BotMaintenanceRecovery.Scope.PROFILE,
        entity_key="profile:42",
        now=now + timedelta(minutes=2),
    )
    requeued.refresh_from_db()
    assert requeued.last_success_at == now + timedelta(minutes=2)


@pytest.mark.django_db
def test_population_demand_recovery_blocks_due_claim_until_requeued() -> None:
    from gameplay.services.virtual_player_core.population_runtime import claim_next_population_recompute_demand

    now = timezone.now()
    demand = BotPopulationRecomputeDemand.objects.create(
        region="north",
        prestige_band="newbie",
        requested_revision=1,
        available_at=now,
    )
    record_recovery_failure(
        scope="population_cell",
        entity_key=str(demand.id),
        failure_code=RecoveryFailureClass.PROGRAMMER_ERROR,
        error=RuntimeError("poisoned population cell"),
        now=now,
        policy=RecoveryPolicy(retry_base_seconds=60, retry_max_seconds=60, quarantine_after_failures=5),
    )

    assert claim_next_population_recompute_demand(now=now + timedelta(seconds=30)) is None

    requeue_recovery(
        scope="population_cell",
        entity_key=str(demand.id),
        now=now + timedelta(seconds=31),
        reason="population_cell_repaired",
    )
    claim = claim_next_population_recompute_demand(now=now + timedelta(seconds=31))

    assert claim is not None
    assert claim.demand_id == demand.id


@pytest.mark.django_db
def test_same_programmer_failure_digest_opens_a_path_circuit() -> None:
    now = timezone.now()
    policy = RecoveryPolicy(
        retry_base_seconds=10,
        retry_max_seconds=60,
        quarantine_after_failures=5,
        circuit_failure_threshold=3,
        circuit_window=timedelta(minutes=10),
    )
    for entity_key in ("profile:1", "profile:2", "profile:3"):
        record_recovery_failure(
            scope=BotMaintenanceRecovery.Scope.PROFILE,
            entity_key=entity_key,
            failure_code=RecoveryFailureClass.PROGRAMMER_ERROR,
            error=RuntimeError("same planner defect"),
            now=now,
            policy=policy,
        )

    assert recovery_circuit_is_open(path="profile", now=now)
    circuit = BotMaintenanceRecovery.objects.get(
        scope=BotMaintenanceRecovery.Scope.PROFILE,
        entity_key="circuit:profile",
    )
    assert circuit.status == BotMaintenanceRecovery.Status.QUARANTINED
    assert circuit.payload["affected_entity_count"] == 3

    requeue_recovery(
        scope=BotMaintenanceRecovery.Scope.PROFILE,
        entity_key="circuit:profile",
        now=now + timedelta(minutes=1),
        reason="planner_repaired",
    )
    assert not recovery_circuit_is_open(path="profile", now=now + timedelta(minutes=1))


@pytest.mark.django_db
def test_population_programmer_circuit_isolated_from_profile_path() -> None:
    now = timezone.now()
    policy = RecoveryPolicy(
        quarantine_after_failures=5,
        circuit_failure_threshold=3,
        circuit_window=timedelta(minutes=10),
    )
    for entity_key in ("1", "2", "3"):
        record_recovery_failure(
            scope=BotMaintenanceRecovery.Scope.POPULATION_CELL,
            entity_key=entity_key,
            failure_code=RecoveryFailureClass.PROGRAMMER_ERROR,
            error=RuntimeError("same population planner defect"),
            now=now,
            policy=policy,
            circuit_path="population",
        )

    assert recovery_circuit_is_open(path="population", now=now)
    assert not recovery_circuit_is_open(path="profile", now=now)
    circuit = BotMaintenanceRecovery.objects.get(
        scope=BotMaintenanceRecovery.Scope.POPULATION_CELL,
        entity_key="circuit:population",
    )
    assert circuit.payload["circuit_scope"] == BotMaintenanceRecovery.Scope.POPULATION_CELL


@pytest.mark.django_db
def test_recovery_management_command_lists_and_requeues_without_direct_row_mutation() -> None:
    now = timezone.now()
    record_recovery_failure(
        scope=BotMaintenanceRecovery.Scope.PROFILE,
        entity_key="42",
        failure_code=RecoveryFailureClass.PROGRAMMER_ERROR,
        error=RuntimeError("poisoned profile"),
        now=now,
    )

    output = StringIO()
    call_command(
        "requeue_virtual_player_recovery",
        "--list",
        "--scope",
        BotMaintenanceRecovery.Scope.PROFILE,
        stdout=output,
    )
    assert "entity_key=42" in output.getvalue()

    call_command(
        "requeue_virtual_player_recovery",
        "--scope",
        BotMaintenanceRecovery.Scope.PROFILE,
        "--entity-key",
        "42",
        "--reason",
        "test_repair",
        stdout=StringIO(),
    )
    row = BotMaintenanceRecovery.objects.get(
        scope=BotMaintenanceRecovery.Scope.PROFILE,
        entity_key="42",
    )
    assert row.status == BotMaintenanceRecovery.Status.REQUEUED
    assert row.payload["requeue_reason"] == "test_repair"
