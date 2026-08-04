from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from gameplay.models import BotVirtualPlayerHealth
from gameplay.services.virtual_player_core import health


@pytest.mark.django_db
@override_settings(
    VIRTUAL_PLAYER_HEALTH_FAILURE_THRESHOLD=3,
    VIRTUAL_PLAYER_HEALTH_RECOVERY_SUCCESS_THRESHOLD=2,
)
def test_transient_health_circuit_recovers_after_clean_probes() -> None:
    start = timezone.now()
    for index in range(3):
        snapshot = health.retryable_failure(
            failure_code="profile_dependency_unavailable",
            error="temporary dependency failure",
            now=start + timedelta(seconds=index),
        )

    assert snapshot.status == BotVirtualPlayerHealth.Status.DEGRADED
    row = BotVirtualPlayerHealth.objects.get(key=BotVirtualPlayerHealth.GLOBAL_KEY)
    probe_at = row.next_probe_at
    assert probe_at is not None
    assert health.reconciliation_deferred_until(now=start) == probe_at

    recovering = health.reconciliation_success(now=probe_at)
    assert recovering is not None
    assert recovering.status == BotVirtualPlayerHealth.Status.RECOVERING

    recovery_probe_at = recovering.next_probe_at
    assert recovery_probe_at is not None
    recovered = health.reconciliation_success(now=recovery_probe_at)
    assert recovered is not None
    assert recovered.status == BotVirtualPlayerHealth.Status.HEALTHY
    assert recovered.next_probe_at is None


@pytest.mark.django_db
@pytest.mark.parametrize("failure_count", [1, 2])
@override_settings(VIRTUAL_PLAYER_HEALTH_FAILURE_THRESHOLD=3)
def test_clean_success_resets_non_consecutive_failure_streak(failure_count: int) -> None:
    start = timezone.now()
    for index in range(failure_count):
        health.retryable_failure(
            failure_code="profile_dependency_unavailable",
            error="temporary dependency failure",
            now=start + timedelta(seconds=index),
        )

    health.reconciliation_success(now=start + timedelta(minutes=1))
    row = BotVirtualPlayerHealth.objects.get(key=BotVirtualPlayerHealth.GLOBAL_KEY)

    assert row.retryable_failure_streak == 0
    assert row.status == BotVirtualPlayerHealth.Status.HEALTHY
