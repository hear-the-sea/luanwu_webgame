from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import Any
from unittest.mock import Mock

import pytest
from django.db import transaction
from django.utils import timezone

from gameplay.models import BotProfile, BotRuntimeRoutingState
from gameplay.services.manor.core import ensure_manor
from gameplay.services.virtual_player_core import profile_management, profile_store
from gameplay.services.virtual_player_core.policy_registry import release_configured_policy_operation

pytestmark = pytest.mark.skip(reason="Gate C V1 enrollment and repair workflow retired after the policy 2 cutover")

RECOVERY_BASIS = "incident-gate-c-repair-001"


class _RollbackRepair(Exception):
    pass


def _create_v1_profile(django_user_model) -> BotProfile:
    now = timezone.now()
    user = django_user_model(username="gate_c_repairs_profile")
    user.set_password("pass123")
    user._signup_region = "north"
    user.save()
    return BotProfile.objects.create(
        manor=ensure_manor(user),
        archetype=BotProfile.Archetype.BALANCED,
        state=BotProfile.State.ACTIVE,
        prestige_band="newbie",
        growth_seed=94_001,
        next_growth_at=now,
        abandon_at=now + timedelta(days=30),
        retire_at=now + timedelta(days=60),
    )


@pytest.fixture
def repairable_v2_profile(django_user_model) -> tuple[BotProfile, dict[str, Any]]:
    BotRuntimeRoutingState.objects.create(
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.V2_CUTOVER,
    )
    release_configured_policy_operation(version=1, apply=True)
    profile = _create_v1_profile(django_user_model)

    enrolled = profile_management.enroll_virtual_players_batch(
        batch_size=10,
        apply=True,
    )
    profile.refresh_from_db()
    assert enrolled.changed == 1
    assert profile.engine_version == 2
    assert profile.development_profile

    return profile, dict(profile.development_profile)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("identity_field", "stale_value"),
    [
        ("growth_seed", 94_002),
        ("archetype", BotProfile.Archetype.RICH),
        ("engine_version", 1),
        ("rng_version", 2),
        ("plan_schema_version", 2),
        ("policy_version", 2),
        ("policy_checksum", "f" * 64),
    ],
)
def test_plan_store_cas_rejects_each_stale_identity_field(
    repairable_v2_profile: tuple[BotProfile, dict[str, Any]],
    identity_field: str,
    stale_value: object,
) -> None:
    profile, generated_plan = repairable_v2_profile
    identity = profile_store.get_profile_plan_identity(profile.pk)
    assert identity is not None
    stale_identity = replace(identity, **{identity_field: stale_value})
    BotProfile.objects.filter(pk=profile.pk).update(development_profile={})

    with pytest.raises(
        profile_store.ProfileStateConflict,
        match="plan identity changed while repair was prepared",
    ):
        profile_store.repair_profile_plan(
            profile.pk,
            expected_plan_schema_version=1,
            expected_identity=stale_identity,
            development_profile=generated_plan,
            apply=True,
        )

    profile.refresh_from_db()
    assert profile.development_profile == {}


def _prepare_required_repair(profile: BotProfile, repair_kind: str) -> None:
    if repair_kind == "plan":
        BotProfile.objects.filter(pk=profile.pk).update(development_profile={})
    else:
        BotProfile.objects.filter(pk=profile.pk).update(rng_version=99)
    profile.refresh_from_db()


def _run_repair(
    profile: BotProfile,
    *,
    repair_kind: str,
    apply: bool,
) -> profile_management.BatchOperationSummary:
    if repair_kind == "plan":
        return profile_management.repair_virtual_player_plan(
            profile_id=profile.pk,
            expected_plan_schema_version=1,
            recovery_basis=RECOVERY_BASIS,
            apply=apply,
        )
    return profile_management.repair_virtual_player_rng(
        profile_id=profile.pk,
        expected_rng_version=99 if profile.rng_version == 99 else 1,
        target_rng_version=1,
        recovery_basis=RECOVERY_BASIS,
        apply=apply,
    )


def _assert_repair_required(profile: BotProfile, repair_kind: str) -> None:
    profile.refresh_from_db()
    if repair_kind == "plan":
        assert profile.development_profile == {}
    else:
        assert profile.rng_version == 99


@pytest.mark.django_db
@pytest.mark.parametrize("repair_kind", ["plan", "rng"])
def test_repair_dry_run_does_not_log_success(
    repairable_v2_profile: tuple[BotProfile, dict[str, Any]],
    repair_kind: str,
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks,
) -> None:
    profile, _generated_plan = repairable_v2_profile
    _prepare_required_repair(profile, repair_kind)
    info = Mock()
    monkeypatch.setattr(profile_management.logger, "info", info)

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        summary = _run_repair(profile, repair_kind=repair_kind, apply=False)

    assert summary.changed == 1
    assert callbacks == []
    info.assert_not_called()
    _assert_repair_required(profile, repair_kind)


@pytest.mark.django_db
@pytest.mark.parametrize("repair_kind", ["plan", "rng"])
def test_repair_noop_does_not_log_success(
    repairable_v2_profile: tuple[BotProfile, dict[str, Any]],
    repair_kind: str,
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks,
) -> None:
    profile, _generated_plan = repairable_v2_profile
    info = Mock()
    monkeypatch.setattr(profile_management.logger, "info", info)

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        summary = _run_repair(profile, repair_kind=repair_kind, apply=True)

    assert summary.changed == 0
    assert summary.skipped == 1
    assert callbacks == []
    info.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("repair_kind", ["plan", "rng"])
def test_repair_cas_conflict_rolls_back_and_does_not_log_success(
    repairable_v2_profile: tuple[BotProfile, dict[str, Any]],
    repair_kind: str,
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks,
) -> None:
    profile, _generated_plan = repairable_v2_profile
    _prepare_required_repair(profile, repair_kind)
    original_seed = profile.growth_seed
    info = Mock()
    monkeypatch.setattr(profile_management.logger, "info", info)

    if repair_kind == "plan":
        real_store_repair = profile_management.repair_profile_plan

        def stale_store_repair(profile_id: int, **kwargs: Any):
            BotProfile.objects.filter(pk=profile_id).update(growth_seed=original_seed + 1)
            return real_store_repair(profile_id, **kwargs)

        monkeypatch.setattr(
            profile_management,
            "repair_profile_plan",
            stale_store_repair,
        )
    else:
        real_store_repair = profile_management.repair_profile_rng

        def stale_store_repair(profile_id: int, **kwargs: Any):
            BotProfile.objects.filter(pk=profile_id).update(rng_version=98)
            return real_store_repair(profile_id, **kwargs)

        monkeypatch.setattr(
            profile_management,
            "repair_profile_rng",
            stale_store_repair,
        )

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(profile_store.ProfileStateConflict):
            _run_repair(profile, repair_kind=repair_kind, apply=True)

    assert callbacks == []
    info.assert_not_called()
    _assert_repair_required(profile, repair_kind)
    profile.refresh_from_db()
    assert profile.growth_seed == original_seed


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("repair_kind", ["plan", "rng"])
def test_repair_outer_transaction_rollback_does_not_log_success(
    repairable_v2_profile: tuple[BotProfile, dict[str, Any]],
    repair_kind: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile, _generated_plan = repairable_v2_profile
    _prepare_required_repair(profile, repair_kind)
    info = Mock()
    monkeypatch.setattr(profile_management.logger, "info", info)

    with pytest.raises(_RollbackRepair):
        with transaction.atomic():
            summary = _run_repair(profile, repair_kind=repair_kind, apply=True)
            assert summary.changed == 1
            raise _RollbackRepair

    info.assert_not_called()
    _assert_repair_required(profile, repair_kind)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("repair_kind", "expected_event"),
    [
        ("plan", "virtual_player_plan_repaired"),
        ("rng", "virtual_player_rng_repaired"),
    ],
)
def test_committed_repair_logs_success_with_recovery_basis(
    repairable_v2_profile: tuple[BotProfile, dict[str, Any]],
    repair_kind: str,
    expected_event: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile, generated_plan = repairable_v2_profile
    _prepare_required_repair(profile, repair_kind)
    info = Mock()
    monkeypatch.setattr(profile_management.logger, "info", info)

    summary = _run_repair(profile, repair_kind=repair_kind, apply=True)

    assert summary.changed == 1
    info.assert_called_once()
    extra = info.call_args.kwargs["extra"]
    assert extra["event"] == expected_event
    assert extra["recovery_basis"] == RECOVERY_BASIS
    profile.refresh_from_db()
    if repair_kind == "plan":
        assert profile.development_profile == generated_plan
    else:
        assert profile.rng_version == 1
