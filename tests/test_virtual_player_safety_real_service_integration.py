from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest
from django.db import close_old_connections, connection
from django.utils import timezone

from gameplay.models import BotSafetyMetricWindow
from gameplay.services.virtual_player_core import safety_monitor, safety_preflight
from gameplay.services.virtual_player_core.safety_metrics import ECONOMY_CAP_BREACH_METRIC, HARD_CONSTRAINT_METRIC
from gameplay.services.virtual_player_core.safety_provider import LateSafetyMetricEventError, record_safety_metric_event

pytestmark = [pytest.mark.integration]

HOURLY_START = datetime(2026, 7, 27, 1, 0, tzinfo=UTC)


@pytest.mark.django_db(transaction=True)
def test_mysql_database_clock_ignores_the_session_timezone() -> None:
    if connection.vendor != "mysql":
        pytest.fail("real-service safety tests require MySQL")

    with connection.cursor() as cursor:
        cursor.execute("SELECT @@session.time_zone")
        original_timezone = cursor.fetchone()[0]
        cursor.execute("SET time_zone = '+08:00'")
    try:
        before = timezone.now().astimezone(UTC)
        database_now = safety_preflight._database_utc_now()
        after = timezone.now().astimezone(UTC)
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SET time_zone = %s", [original_timezone])

    assert database_now.tzinfo is UTC
    assert before <= database_now <= after


@pytest.mark.django_db(transaction=True)
def test_atomic_finalizer_serializes_event_writer_on_real_database(monkeypatch) -> None:
    if connection.vendor != "mysql":
        pytest.fail("real-service safety tests require MySQL")

    record_safety_metric_event(
        event_id="atomic-finalize:initial",
        metric_name=HARD_CONSTRAINT_METRIC,
        occurred_at=HOURLY_START + timedelta(minutes=10),
        dimensions={},
        value=1,
    )
    builder_holds_window = threading.Event()
    release_builder = threading.Event()
    writer_started = threading.Event()
    writer_finished = threading.Event()
    errors: list[BaseException] = []
    original_builder = safety_monitor._build_provider_snapshot

    def blocking_builder(aggregation):
        builder_holds_window.set()
        if not release_builder.wait(timeout=10):
            raise TimeoutError("atomic snapshot builder was not released")
        return original_builder(aggregation)

    monkeypatch.setattr(
        safety_monitor,
        "_build_provider_snapshot",
        blocking_builder,
    )

    def finalize_worker() -> None:
        close_old_connections()
        try:
            safety_monitor.finalize_due_safety_windows(
                now=HOURLY_START + timedelta(hours=1, minutes=5),
                limit=1,
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            close_old_connections()

    def write_worker() -> None:
        close_old_connections()
        writer_started.set()
        try:
            record_safety_metric_event(
                event_id="atomic-finalize:concurrent",
                metric_name=ECONOMY_CAP_BREACH_METRIC,
                occurred_at=HOURLY_START + timedelta(minutes=20),
                dimensions={},
                value=1,
            )
        except LateSafetyMetricEventError:
            pass
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            writer_finished.set()
            close_old_connections()

    finalizer = threading.Thread(target=finalize_worker, daemon=True)
    writer = threading.Thread(target=write_worker, daemon=True)
    finalizer.start()
    assert builder_holds_window.wait(timeout=10)
    writer.start()
    assert writer_started.wait(timeout=10)
    assert not writer_finished.wait(timeout=0.5)

    release_builder.set()
    finalizer.join(timeout=30)
    writer.join(timeout=30)

    assert not finalizer.is_alive()
    assert not writer.is_alive()
    assert errors == []
    window = BotSafetyMetricWindow.objects.get(
        kind="hourly",
        window_start_at=HOURLY_START,
    )
    assert window.snapshot["metrics"]["hard_constraint_violation_count"] == 1
    assert window.snapshot["metrics"]["economy_cap_breach_count"] == 0
