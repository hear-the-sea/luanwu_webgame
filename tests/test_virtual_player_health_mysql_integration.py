import pytest
from django.db import connection
from django.test import override_settings
from django.utils import timezone

from gameplay.services.virtual_player_core import health


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
@override_settings(VIRTUAL_PLAYER_HEALTH_FAILURE_THRESHOLD=1)
def test_reconciliation_deferred_until_is_safe_outside_atomic_on_mysql() -> None:
    if connection.vendor != "mysql":
        pytest.skip("requires MySQL select_for_update semantics")

    start = timezone.now()
    health.retryable_failure(
        failure_code="profile_dependency_unavailable",
        error="temporary dependency failure",
        now=start,
    )

    deferred_until = health.reconciliation_deferred_until(now=start)

    assert deferred_until is not None
