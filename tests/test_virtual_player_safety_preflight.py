from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from django.db import DatabaseError, connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from gameplay.models import BotSafetyMetricEvent
from gameplay.services.virtual_player_core import maintenance, safety_preflight
from gameplay.services.virtual_player_core.contracts import MaintenanceOutcome, MaintenanceTrigger
from gameplay.services.virtual_player_core.safety_metrics import SAFETY_HEARTBEAT_METRIC
from gameplay.services.virtual_player_core.safety_preflight import (
    SAFETY_MONITOR_MAX_AGE,
    SAFETY_MONITOR_STREAM,
    check_v2_development_write_preflight,
)

CHECKED_AT = datetime(2026, 7, 28, 8, 0, tzinfo=UTC)


def _seed_monitor_heartbeat(*, occurred_at: datetime, value: int = 1) -> None:
    BotSafetyMetricEvent.objects.create(
        event_id=(f"safety-heartbeat:{SAFETY_MONITOR_STREAM}:" f"{occurred_at.strftime('%Y%m%dT%H%MZ')}"),
        metric_name=SAFETY_HEARTBEAT_METRIC,
        occurred_at=occurred_at,
        dimensions={"stream": SAFETY_MONITOR_STREAM},
        value=value,
        payload_digest="a" * 64,
    )


@pytest.mark.django_db
def test_database_clock_returns_an_aware_current_utc_value() -> None:
    before = timezone.now().astimezone(UTC)

    database_now = safety_preflight._database_utc_now()

    after = timezone.now().astimezone(UTC)
    assert database_now.tzinfo is UTC
    assert before - timedelta(seconds=1) <= database_now <= after


@pytest.mark.django_db
def test_preflight_allows_only_a_fresh_persisted_monitor_heartbeat() -> None:
    heartbeat_at = CHECKED_AT - SAFETY_MONITOR_MAX_AGE
    _seed_monitor_heartbeat(occurred_at=heartbeat_at)

    with CaptureQueriesContext(connection) as captured:
        result = check_v2_development_write_preflight(now=CHECKED_AT)

    assert result.allowed is True
    assert result.reason == ""
    assert result.checked_at == CHECKED_AT
    assert result.monitor_heartbeat_at == heartbeat_at
    statements = tuple(query["sql"].lstrip().upper() for query in captured)
    assert statements
    assert all(statement.startswith("SELECT") for statement in statements)


@pytest.mark.django_db
def test_preflight_rejects_a_missing_monitor_heartbeat() -> None:
    result = check_v2_development_write_preflight(now=CHECKED_AT)

    assert result.allowed is False
    assert result.reason == "safety_monitor_heartbeat_missing"
    assert result.checked_at == CHECKED_AT
    assert result.monitor_heartbeat_at is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("heartbeat_at", "value", "reason"),
    (
        (
            CHECKED_AT - timedelta(minutes=3),
            1,
            "safety_monitor_heartbeat_stale",
        ),
        (
            CHECKED_AT + timedelta(minutes=1),
            1,
            "safety_monitor_heartbeat_from_future",
        ),
        (
            CHECKED_AT - timedelta(seconds=1),
            0,
            "safety_monitor_heartbeat_invalid",
        ),
    ),
)
def test_preflight_rejects_untrusted_monitor_heartbeat(
    heartbeat_at: datetime,
    value: int,
    reason: str,
) -> None:
    _seed_monitor_heartbeat(occurred_at=heartbeat_at, value=value)

    result = check_v2_development_write_preflight(now=CHECKED_AT)

    assert result.allowed is False
    assert result.reason == reason
    assert result.checked_at == CHECKED_AT
    assert result.monitor_heartbeat_at == heartbeat_at


@pytest.mark.django_db
@pytest.mark.parametrize(
    "invalid_update",
    (
        {"event_id": "not-a-canonical-heartbeat"},
        {"dimensions": {"stream": SAFETY_MONITOR_STREAM, "result": "all"}},
        {"occurred_at": CHECKED_AT - timedelta(seconds=1)},
    ),
)
def test_preflight_rejects_noncanonical_monitor_events(invalid_update) -> None:
    heartbeat_at = CHECKED_AT - timedelta(minutes=1)
    _seed_monitor_heartbeat(occurred_at=heartbeat_at)
    BotSafetyMetricEvent.objects.update(**invalid_update)

    result = check_v2_development_write_preflight(now=CHECKED_AT)

    assert result.allowed is False
    assert result.reason == "safety_monitor_heartbeat_invalid"


@pytest.mark.django_db
def test_preflight_fails_closed_when_the_provider_cannot_be_read(monkeypatch) -> None:
    def unavailable_database_clock() -> datetime:
        raise DatabaseError("provider unavailable")

    monkeypatch.setattr(
        "gameplay.services.virtual_player_core.safety_preflight._database_utc_now",
        unavailable_database_clock,
    )

    result = check_v2_development_write_preflight()

    assert result.allowed is False
    assert result.reason == "safety_provider_unreadable"
    assert result.checked_at is None
    assert result.monitor_heartbeat_at is None


@pytest.mark.django_db
def test_v2_maintenance_pauses_before_attempt_or_business_work(monkeypatch) -> None:
    def unexpected_call(*_args, **_kwargs):
        raise AssertionError("preflight rejection must happen before V2 work")

    monkeypatch.setattr(maintenance, "start_maintenance_attempt", unexpected_call)
    monkeypatch.setattr(
        maintenance,
        "build_virtual_player_v2_maintenance_plan",
        unexpected_call,
    )

    result = maintenance.maintain_virtual_player_v2(
        41,
        trigger=MaintenanceTrigger.SCHEDULED,
    )

    assert result.outcome is MaintenanceOutcome.PAUSED
    assert result.reason == "safety_monitor_heartbeat_missing"
    assert not BotSafetyMetricEvent.objects.exists()
