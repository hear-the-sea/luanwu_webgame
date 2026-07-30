from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Iterator
from uuid import uuid4

import pytest
from django.db import connection, transaction
from django.utils import timezone

from gameplay.models import BotExternalStrengthReconciliation
from gameplay.services.virtual_player_core import external_reconciliation

pytestmark = pytest.mark.django_db

_SUCCESS_EVENT = "virtual_player_external_reconciliation_requeued"
_PERSISTED_FIELDS = tuple(field.attname for field in BotExternalStrengthReconciliation._meta.concrete_fields)


def _create_quarantined_reconciliation(
    *,
    phase: str = BotExternalStrengthReconciliation.Phase.PROFILE,
) -> BotExternalStrengthReconciliation:
    now = timezone.now()
    population_phase = phase == BotExternalStrengthReconciliation.Phase.POPULATION
    return BotExternalStrengthReconciliation.objects.create(
        profile_id=7_001,
        domain_event_kind="gate_c_requeue_test",
        domain_event_id=uuid4().hex,
        origin_committed_at=now - timedelta(minutes=10),
        pre_strength_summary={"score": 41, "source": "gate-c-test"},
        pre_prestige_band="newbie",
        status=BotExternalStrengthReconciliation.Status.QUARANTINED,
        profile_attempt_count=8 if population_phase else 3,
        population_attempt_count=7 if population_phase else 0,
        available_at=now + timedelta(days=1),
        profile_completed_at=now - timedelta(minutes=5) if population_phase else None,
        result_summary={"profile_changed": population_phase, "sentinel": "preserve"},
        quarantined_at=now - timedelta(minutes=1),
        quarantined_phase=phase,
        failure_code=f"{phase}_contract_error",
        last_error_digest="d" * 64,
    )


def _phase_attempt_count(
    reconciliation: BotExternalStrengthReconciliation,
) -> int:
    if reconciliation.quarantined_phase == BotExternalStrengthReconciliation.Phase.POPULATION:
        return reconciliation.population_attempt_count
    return reconciliation.profile_attempt_count


def _requeue(
    reconciliation: BotExternalStrengthReconciliation,
    **overrides: Any,
) -> external_reconciliation.ReconciliationOperationSummary:
    kwargs = {
        "reconciliation_id": reconciliation.pk,
        "expected_failure_code": reconciliation.failure_code,
        "expected_attempt_count": _phase_attempt_count(reconciliation),
        "recovery_basis": "incident-gate-c-requeue-001",
        "apply": True,
    }
    kwargs.update(overrides)
    return external_reconciliation.requeue_quarantined_reconciliation_operation(**kwargs)


def _snapshot(reconciliation_id: int) -> dict[str, Any]:
    reconciliation = BotExternalStrengthReconciliation.objects.get(pk=reconciliation_id)
    return {field_name: getattr(reconciliation, field_name) for field_name in _PERSISTED_FIELDS}


def _success_records(caplog) -> list[logging.LogRecord]:
    return [record for record in caplog.records if getattr(record, "event", None) == _SUCCESS_EVENT]


def _capture_success_logs(caplog) -> None:
    caplog.set_level(logging.INFO, logger=external_reconciliation.logger.name)


@contextmanager
def _sqlite_check_constraints_ignored() -> Iterator[None]:
    if connection.vendor != "sqlite":
        pytest.skip("corrupt-row persistence fixtures require the hermetic SQLite gate")
    with connection.cursor() as cursor:
        cursor.execute("PRAGMA ignore_check_constraints = ON")
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA ignore_check_constraints = OFF")


def _force_corrupt_state(
    reconciliation_id: int,
    **changes: Any,
) -> None:
    # Exercise service-level fail-closed checks against rows that predate DB constraints.
    with _sqlite_check_constraints_ignored():
        updated = BotExternalStrengthReconciliation.objects.filter(pk=reconciliation_id).update(**changes)
    assert updated == 1


@pytest.mark.parametrize("expected_attempt_count", [0, 13])
def test_requeue_rejects_attempt_counts_outside_the_frozen_range(
    expected_attempt_count: int,
    caplog,
) -> None:
    reconciliation = _create_quarantined_reconciliation()
    before = _snapshot(reconciliation.pk)
    _capture_success_logs(caplog)

    with pytest.raises(
        external_reconciliation.ExternalReconciliationError,
        match="expected_attempt_count must be between 1 and 12",
    ):
        _requeue(
            reconciliation,
            expected_attempt_count=expected_attempt_count,
        )

    assert _snapshot(reconciliation.pk) == before
    assert _success_records(caplog) == []


@pytest.mark.parametrize(
    ("field_name", "blank_value"),
    [
        ("expected_failure_code", ""),
        ("expected_failure_code", " \t "),
        ("recovery_basis", ""),
        ("recovery_basis", " \t "),
    ],
)
def test_requeue_rejects_blank_required_audit_inputs(
    field_name: str,
    blank_value: str,
    caplog,
) -> None:
    reconciliation = _create_quarantined_reconciliation()
    before = _snapshot(reconciliation.pk)
    _capture_success_logs(caplog)

    with pytest.raises(
        external_reconciliation.ExternalReconciliationError,
        match=rf"{field_name} must not be blank",
    ):
        _requeue(reconciliation, **{field_name: blank_value})

    assert _snapshot(reconciliation.pk) == before
    assert _success_records(caplog) == []


def test_requeue_rejects_a_missing_reconciliation_without_success_log(caplog) -> None:
    _capture_success_logs(caplog)

    with pytest.raises(
        external_reconciliation.ExternalReconciliationConflict,
        match=r"reconciliation 999999 does not exist",
    ):
        external_reconciliation.requeue_quarantined_reconciliation_operation(
            reconciliation_id=999_999,
            expected_failure_code="profile_contract_error",
            expected_attempt_count=3,
            recovery_basis="incident-gate-c-missing-row",
            apply=True,
        )

    assert _success_records(caplog) == []


def test_requeue_rejects_a_non_quarantined_reconciliation(caplog) -> None:
    reconciliation = _create_quarantined_reconciliation()
    BotExternalStrengthReconciliation.objects.filter(pk=reconciliation.pk).update(
        status=BotExternalStrengthReconciliation.Status.PENDING_PROFILE,
        quarantined_at=None,
        quarantined_phase="",
        failure_code="",
        last_error_digest="",
    )
    before = _snapshot(reconciliation.pk)
    _capture_success_logs(caplog)

    with pytest.raises(
        external_reconciliation.ExternalReconciliationConflict,
        match=rf"reconciliation {reconciliation.pk} is not quarantined",
    ):
        _requeue(
            reconciliation,
            expected_failure_code="profile_contract_error",
        )

    assert _snapshot(reconciliation.pk) == before
    assert _success_records(caplog) == []


@pytest.mark.parametrize(
    "claim_field",
    ["claim_token", "claimed_at", "claim_expires_at"],
)
def test_requeue_rejects_any_residual_claim_field(
    claim_field: str,
    caplog,
) -> None:
    reconciliation = _create_quarantined_reconciliation()
    now = timezone.now()
    claim_values = {
        "claim_token": uuid4(),
        "claimed_at": now,
        "claim_expires_at": now + timedelta(minutes=5),
    }
    _force_corrupt_state(
        reconciliation.pk,
        **{claim_field: claim_values[claim_field]},
    )
    before = _snapshot(reconciliation.pk)
    _capture_success_logs(caplog)

    with pytest.raises(
        external_reconciliation.ExternalReconciliationConflict,
        match=rf"reconciliation {reconciliation.pk} has an active or corrupt claim",
    ):
        _requeue(reconciliation)

    assert _snapshot(reconciliation.pk) == before
    assert _success_records(caplog) == []


def test_requeue_rejects_an_invalid_quarantined_phase(caplog) -> None:
    reconciliation = _create_quarantined_reconciliation()
    BotExternalStrengthReconciliation.objects.filter(pk=reconciliation.pk).update(quarantined_phase="invalid")
    before = _snapshot(reconciliation.pk)
    _capture_success_logs(caplog)

    with pytest.raises(
        external_reconciliation.ExternalReconciliationConflict,
        match=rf"reconciliation {reconciliation.pk} has an invalid quarantined phase",
    ):
        _requeue(reconciliation)

    assert _snapshot(reconciliation.pk) == before
    assert _success_records(caplog) == []


@pytest.mark.parametrize(
    "invalid_field",
    [
        "profile_completed_at",
        "population_handoff_completed_at",
        "applied_at",
        "population_attempt_count",
    ],
)
def test_requeue_rejects_inconsistent_profile_phase_progress(
    invalid_field: str,
    caplog,
) -> None:
    reconciliation = _create_quarantined_reconciliation(phase=BotExternalStrengthReconciliation.Phase.PROFILE)
    invalid_value: datetime | int
    if invalid_field == "population_attempt_count":
        invalid_value = 1
    else:
        invalid_value = timezone.now()
    _force_corrupt_state(reconciliation.pk, **{invalid_field: invalid_value})
    before = _snapshot(reconciliation.pk)
    _capture_success_logs(caplog)

    with pytest.raises(
        external_reconciliation.ExternalReconciliationConflict,
        match=rf"reconciliation {reconciliation.pk} has inconsistent profile-phase progress",
    ):
        _requeue(reconciliation)

    assert _snapshot(reconciliation.pk) == before
    assert _success_records(caplog) == []


@pytest.mark.parametrize(
    "invalid_field",
    [
        "profile_completed_at",
        "population_handoff_completed_at",
        "applied_at",
    ],
)
def test_requeue_rejects_inconsistent_population_phase_progress(
    invalid_field: str,
    caplog,
) -> None:
    reconciliation = _create_quarantined_reconciliation(phase=BotExternalStrengthReconciliation.Phase.POPULATION)
    invalid_value = None if invalid_field == "profile_completed_at" else timezone.now()
    _force_corrupt_state(reconciliation.pk, **{invalid_field: invalid_value})
    before = _snapshot(reconciliation.pk)
    _capture_success_logs(caplog)

    with pytest.raises(
        external_reconciliation.ExternalReconciliationConflict,
        match=rf"reconciliation {reconciliation.pk} has inconsistent population-phase progress",
    ):
        _requeue(reconciliation)

    assert _snapshot(reconciliation.pk) == before
    assert _success_records(caplog) == []


def test_requeue_rejects_stale_failure_and_attempt_expectations_without_writes(
    caplog,
) -> None:
    reconciliation = _create_quarantined_reconciliation()
    before = _snapshot(reconciliation.pk)
    _capture_success_logs(caplog)

    with pytest.raises(
        external_reconciliation.ExternalReconciliationConflict,
        match=rf"reconciliation {reconciliation.pk} failure code changed",
    ):
        _requeue(reconciliation, expected_failure_code="different_failure")
    with pytest.raises(
        external_reconciliation.ExternalReconciliationConflict,
        match=rf"reconciliation {reconciliation.pk} attempt count changed",
    ):
        _requeue(reconciliation, expected_attempt_count=2)

    assert _snapshot(reconciliation.pk) == before
    assert _success_records(caplog) == []


@pytest.mark.parametrize(
    ("phase", "pending_status", "reset_field"),
    [
        (
            BotExternalStrengthReconciliation.Phase.PROFILE,
            BotExternalStrengthReconciliation.Status.PENDING_PROFILE,
            "profile_attempt_count",
        ),
        (
            BotExternalStrengthReconciliation.Phase.POPULATION,
            BotExternalStrengthReconciliation.Status.PENDING_POPULATION,
            "population_attempt_count",
        ),
    ],
)
def test_requeue_dry_run_and_apply_obey_the_phase_field_whitelist(
    phase: str,
    pending_status: str,
    reset_field: str,
    caplog,
) -> None:
    reconciliation = _create_quarantined_reconciliation(phase=phase)
    before = _snapshot(reconciliation.pk)
    _capture_success_logs(caplog)

    preview = _requeue(reconciliation, apply=False)

    assert preview.scanned == 1
    assert preview.locked == 0
    assert preview.changed == 1
    assert preview.skipped == 0
    assert preview.failed == 0
    assert preview.reasons == ()
    assert preview.reconciliation_id == reconciliation.pk
    assert _snapshot(reconciliation.pk) == before
    assert _success_records(caplog) == []

    applied = _requeue(reconciliation)
    after = _snapshot(reconciliation.pk)
    changed_fields = {field_name for field_name in _PERSISTED_FIELDS if before[field_name] != after[field_name]}

    assert applied.scanned == 1
    assert applied.locked == 0
    assert applied.changed == 1
    assert applied.skipped == 0
    assert applied.failed == 0
    assert applied.reasons == ()
    assert applied.reconciliation_id == reconciliation.pk
    assert changed_fields == {
        "status",
        reset_field,
        "available_at",
        "quarantined_at",
        "quarantined_phase",
        "failure_code",
        "last_error_digest",
        "updated_at",
    }
    assert after["status"] == pending_status
    assert after[reset_field] == 0
    assert after["claim_token"] is None
    assert after["claimed_at"] is None
    assert after["claim_expires_at"] is None
    assert after["quarantined_at"] is None
    assert after["quarantined_phase"] == ""
    assert after["failure_code"] == ""
    assert after["last_error_digest"] == ""


@pytest.mark.django_db(transaction=True)
def test_requeue_success_log_is_emitted_only_after_commit_with_original_evidence(
    caplog,
) -> None:
    reconciliation = _create_quarantined_reconciliation()
    original_quarantined_at = reconciliation.quarantined_at
    original_error_digest = reconciliation.last_error_digest
    _capture_success_logs(caplog)

    with transaction.atomic():
        _requeue(
            reconciliation,
            recovery_basis="incident-gate-c-audit-evidence",
        )
        assert _success_records(caplog) == []
        assert (
            BotExternalStrengthReconciliation.objects.values_list("status", flat=True).get(pk=reconciliation.pk)
            == BotExternalStrengthReconciliation.Status.PENDING_PROFILE
        )

    records = _success_records(caplog)
    assert len(records) == 1
    record = records[0]
    assert record.reconciliation_id == reconciliation.pk
    assert record.profile_id == reconciliation.profile_id
    assert record.reconciliation_phase == BotExternalStrengthReconciliation.Phase.PROFILE
    assert record.expected_failure_code == "profile_contract_error"
    assert record.expected_attempt_count == 3
    assert record.recovery_basis == "incident-gate-c-audit-evidence"
    assert record.quarantined_at == original_quarantined_at
    assert record.last_error_digest == original_error_digest


@pytest.mark.django_db(transaction=True)
def test_requeue_outer_transaction_rollback_restores_row_and_emits_no_success_log(
    caplog,
) -> None:
    reconciliation = _create_quarantined_reconciliation()
    before = _snapshot(reconciliation.pk)
    _capture_success_logs(caplog)

    with pytest.raises(RuntimeError, match="forced outer rollback"):
        with transaction.atomic():
            _requeue(reconciliation)
            assert _success_records(caplog) == []
            assert (
                BotExternalStrengthReconciliation.objects.values_list("status", flat=True).get(pk=reconciliation.pk)
                == BotExternalStrengthReconciliation.Status.PENDING_PROFILE
            )
            raise RuntimeError("forced outer rollback")

    assert _snapshot(reconciliation.pk) == before
    assert _success_records(caplog) == []


@pytest.mark.django_db(transaction=True)
def test_requeue_repeated_apply_is_a_conflict_without_a_second_write_or_log(
    caplog,
) -> None:
    reconciliation = _create_quarantined_reconciliation()
    _capture_success_logs(caplog)

    _requeue(reconciliation)
    assert len(_success_records(caplog)) == 1
    caplog.clear()
    after_first_apply = _snapshot(reconciliation.pk)

    with pytest.raises(
        external_reconciliation.ExternalReconciliationConflict,
        match=rf"reconciliation {reconciliation.pk} is not quarantined",
    ):
        _requeue(reconciliation)

    assert _snapshot(reconciliation.pk) == after_first_apply
    assert _success_records(caplog) == []
