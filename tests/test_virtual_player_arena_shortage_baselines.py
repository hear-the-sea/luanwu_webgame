from __future__ import annotations

from datetime import UTC, timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from gameplay.models import BotArenaShortageBaseline, BotRuntimeRoutingState, BotSafetyMetricWindow
from gameplay.services.virtual_player_core import safety_baselines, safety_monitor
from gameplay.services.virtual_player_core.safety_metrics import record_arena_shortage

EVIDENCE_CHECKSUM = "a" * 64


def _routing_state(*, maintenance_mode: str) -> BotRuntimeRoutingState:
    return BotRuntimeRoutingState.objects.create(
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=maintenance_mode,
        calibration_routes=[],
        revision=3,
    )


def _freeze(
    *,
    mode: str = "coop",
    prestige_band: str = "junior",
    baseline_ratio: object = "0.100000000000",
    evidence_id: str = "arena-shortage-evidence-20260728",
) -> safety_baselines.ArenaShortageBaselineFreezeResult:
    return safety_baselines.freeze_arena_shortage_baseline(
        mode=mode,
        prestige_band=prestige_band,
        baseline_ratio=baseline_ratio,
        evidence_id=evidence_id,
        evidence_checksum=EVIDENCE_CHECKSUM,
    )


@pytest.mark.django_db
def test_baseline_model_enforces_unique_scope_and_ratio_range() -> None:
    now = timezone.now()
    fields = {
        "mode": "coop",
        "prestige_band": "junior",
        "baseline_ratio": Decimal("0.1"),
        "frozen_at": now,
        "evidence_id": "evidence-1",
        "evidence_checksum": EVIDENCE_CHECKSUM,
        "payload_digest": "b" * 64,
    }
    BotArenaShortageBaseline.objects.create(**fields)

    with pytest.raises(IntegrityError), transaction.atomic():
        BotArenaShortageBaseline.objects.create(**fields)
    with pytest.raises(IntegrityError), transaction.atomic():
        BotArenaShortageBaseline.objects.create(
            **{
                **fields,
                "mode": "tournament",
                "baseline_ratio": Decimal("1.000000000001"),
            }
        )


@pytest.mark.django_db
def test_freeze_is_content_addressed_idempotent_and_conflict_safe() -> None:
    _routing_state(maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.LEGACY_BEFORE_GATE)

    created = _freeze()
    repeated = _freeze(baseline_ratio=Decimal("0.1"))

    assert created.created is True
    assert repeated.created is False
    assert repeated.baseline == created.baseline
    assert created.baseline.baseline_ratio == Decimal("0.100000000000")
    assert len(created.baseline.payload_digest) == 64
    assert created.baseline.frozen_at.tzinfo is not None
    assert BotArenaShortageBaseline.objects.count() == 1

    with pytest.raises(
        safety_baselines.ArenaShortageBaselineConflict,
        match="different frozen content",
    ):
        _freeze(baseline_ratio="0.2")
    assert BotArenaShortageBaseline.objects.count() == 1


@pytest.mark.django_db
def test_new_freeze_is_blocked_after_activation_but_same_payload_replay_is_allowed() -> None:
    state = _routing_state(maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.LEGACY_BEFORE_GATE)
    original = _freeze()
    state.maintenance_mode = BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE
    state.save(update_fields=["maintenance_mode", "updated_at"])

    replay = _freeze()

    assert replay.created is False
    assert replay.baseline == original.baseline
    with pytest.raises(
        safety_baselines.ArenaShortageBaselineActivationBlocked,
        match="before Maintenance V2 activation",
    ):
        _freeze(prestige_band="middle")


@pytest.mark.django_db
def test_cutover_remains_a_pre_activation_freeze_boundary() -> None:
    _routing_state(maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_CUTOVER)

    result = _freeze()

    assert result.created is True


@pytest.mark.django_db
def test_new_freeze_requires_persisted_routing_state() -> None:
    with pytest.raises(
        safety_baselines.ArenaShortageBaselineActivationBlocked,
        match="persisted virtual-player routing is required",
    ):
        _freeze()

    assert not BotArenaShortageBaseline.objects.exists()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"mode": "ranked"}, "tournament or coop"),
        ({"prestige_band": "unknown"}, "configured V2 prestige band"),
        ({"baseline_ratio": 0.1}, "canonical decimal string"),
        ({"baseline_ratio": "1.1"}, "between 0 and 1"),
        ({"baseline_ratio": "0.1234567890123"}, "at most 12 decimal places"),
        ({"evidence_id": "contains spaces"}, "canonical ASCII"),
        ({"evidence_checksum": "bad"}, "64-character hexadecimal"),
    ],
)
def test_baseline_request_validation_is_strict(overrides: dict[str, object], message: str) -> None:
    request = {
        "mode": "coop",
        "prestige_band": "junior",
        "baseline_ratio": "0.1",
        "evidence_id": "evidence-1",
        "evidence_checksum": EVIDENCE_CHECKSUM,
    }

    with pytest.raises(safety_baselines.ArenaShortageBaselineError, match=message):
        safety_baselines.normalize_arena_shortage_baseline_request(**{**request, **overrides})


@pytest.mark.django_db
def test_freeze_command_defaults_to_dry_run() -> None:
    _routing_state(maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.LEGACY_BEFORE_GATE)
    options = {
        "mode": "coop",
        "prestige_band": "junior",
        "baseline_ratio": "0.1",
        "evidence_id": "evidence-1",
        "evidence_checksum": EVIDENCE_CHECKSUM,
        "verbosity": 0,
    }
    dry_run_stdout = StringIO()

    call_command(
        "freeze_virtual_player_arena_shortage_baseline",
        stdout=dry_run_stdout,
        **options,
    )

    assert not BotArenaShortageBaseline.objects.exists()
    assert "mode=dry-run" in dry_run_stdout.getvalue()
    assert "changed=1" in dry_run_stdout.getvalue()

    apply_stdout = StringIO()
    call_command(
        "freeze_virtual_player_arena_shortage_baseline",
        stdout=apply_stdout,
        apply=True,
        **options,
    )

    assert BotArenaShortageBaseline.objects.count() == 1
    assert "mode=apply" in apply_stdout.getvalue()
    assert "payload_digest=" in apply_stdout.getvalue()


@pytest.mark.django_db
def test_finalizer_locks_and_embeds_sorted_frozen_baselines(monkeypatch) -> None:
    state = _routing_state(maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.LEGACY_BEFORE_GATE)
    tournament = _freeze(
        mode="tournament",
        prestige_band="middle",
        baseline_ratio="0.2",
        evidence_id="tournament-evidence",
    ).baseline
    coop = _freeze(
        mode="coop",
        prestige_band="junior",
        baseline_ratio="0.1",
        evidence_id="coop-evidence",
    ).baseline
    state.maintenance_mode = BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE
    state.save(update_fields=["maintenance_mode", "updated_at"])

    now = timezone.now().astimezone(UTC)
    window_start = (now - timedelta(hours=2)).replace(
        minute=0,
        second=0,
        microsecond=0,
    )
    record_arena_shortage(
        operation_id="arena-shortage-operation",
        mode="coop",
        prestige_band="junior",
        missing_count=1,
        capacity=10,
        occurred_at=window_start + timedelta(minutes=10),
    )
    original_lock = safety_baselines.lock_frozen_arena_shortage_baselines
    lock_states: list[bool] = []

    def lock_with_assertion():
        lock_states.append(connection.in_atomic_block)
        return original_lock()

    monkeypatch.setattr(
        safety_baselines,
        "lock_frozen_arena_shortage_baselines",
        lock_with_assertion,
    )

    result = safety_monitor.finalize_due_safety_windows(
        now=window_start + timedelta(hours=1, minutes=5),
        limit=1,
    )

    assert len(result) == 1
    assert lock_states == [True]
    window = BotSafetyMetricWindow.objects.get(window_id=result[0].window_id)
    assert window.snapshot["arena_shortage_baselines"] == [
        {
            "kind": "coop",
            "prestige_band": "junior",
            "baseline_ratio": 0.1,
            "frozen_at": safety_monitor._canonical_timestamp(coop.frozen_at),
            "evidence_id": "coop-evidence",
            "evidence_checksum": EVIDENCE_CHECKSUM,
            "payload_digest": coop.payload_digest,
        },
        {
            "kind": "tournament",
            "prestige_band": "middle",
            "baseline_ratio": 0.2,
            "frozen_at": safety_monitor._canonical_timestamp(tournament.frozen_at),
            "evidence_id": "tournament-evidence",
            "evidence_checksum": EVIDENCE_CHECKSUM,
            "payload_digest": tournament.payload_digest,
        },
    ]

    monkeypatch.setattr(
        safety_baselines,
        "lock_frozen_arena_shortage_baselines",
        lambda: pytest.fail("decision monitor must only read the finalized snapshot"),
    )
    decision = safety_monitor.evaluate_finalized_safety_window(window)
    assert not any(reason.startswith("arena_shortage_baseline_missing") for reason in decision.pause_reasons)


def test_run_safety_monitor_resolves_default_clock_once(monkeypatch) -> None:
    first = timezone.now().astimezone(UTC)
    second = first + timedelta(hours=1)
    clock_values = iter((first, second))
    finalize_calls: list[dict[str, object]] = []
    monitor_calls: list[dict[str, object]] = []

    monkeypatch.setattr(safety_monitor.timezone, "now", lambda: next(clock_values))
    monkeypatch.setattr(
        safety_monitor,
        "finalize_due_safety_windows",
        lambda **kwargs: finalize_calls.append(kwargs) or (),
    )
    monitor_result = object()
    monkeypatch.setattr(
        safety_monitor,
        "monitor_finalized_safety_windows",
        lambda **kwargs: monitor_calls.append(kwargs) or monitor_result,
    )

    result = safety_monitor.run_safety_monitor(limit=7, max_cas_retries=4)

    assert finalize_calls == [{"now": first, "limit": 7}]
    assert monitor_calls == [{"now": first, "limit": 7, "max_cas_retries": 4}]
    assert result.monitor is monitor_result
