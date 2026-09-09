from __future__ import annotations

import threading
from time import monotonic

import pytest
from celery.contrib.testing.worker import start_worker
from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from config.celery import app as celery_app
from gameplay.models import BotProfile
from gameplay.services.virtual_player_core import maintenance
from gameplay.services.virtual_player_core.policy_registry import release_configured_policy_operation
from gameplay.services.virtual_player_core.safety_metrics import record_safety_heartbeat
from gameplay.tasks.virtual_players import scan_virtual_player_maintenance_task
from tests.test_virtual_player_maintenance_concurrency_integration import (
    _activate_v2_maintenance,
    _create_v2_profiles,
    _install_permissive_reference,
    _require_mysql,
)

pytestmark = [pytest.mark.integration, pytest.mark.capacity]

_TASK_BATCH_LIMIT = 200
_SCAN_PERIOD_SECONDS = 3_600.0
_WORKER_SHUTDOWN_TIMEOUT_SECONDS = 60.0
_HEARTBEAT_JOIN_TIMEOUT_SECONDS = 20.0
_QUEUE_CASES = ((1, 200), (3, 500), (5, 1000))


@pytest.fixture
def released_v2_policy(db):
    return release_configured_policy_operation(version=2, apply=True)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(("task_count", "profile_count"), _QUEUE_CASES)
def test_real_timer_maintenance_queue_preserves_fair_batches(
    task_count,
    profile_count,
    released_v2_policy,
    game_data,
    monkeypatch,
) -> None:
    """Measure the scheduled owner through the real broker/worker queue.

    Multiple tasks are published before the first task finishes.  A single
    ``timer_maintenance`` worker consumes them in order, proving that queue
    wait is observable separately from the synchronous maintenance owner and
    that the fair ``last_planned_at`` selection still covers every due
    profile exactly once across the 200/500/1000 capacity matrix.
    """

    _require_mysql()
    _activate_v2_maintenance()
    _install_permissive_reference(monkeypatch)

    now = timezone.now()
    profiles = _create_v2_profiles(
        count=profile_count,
        now=now,
        policy=released_v2_policy,
        include_guest=False,
    )
    profile_ids = {int(profile.id) for profile in profiles}
    record_safety_heartbeat("safety_monitor", now=now)

    selected_batches: list[set[int]] = []
    owner_started_at: list[float] = []
    owner_finished_at: list[float] = []
    oldest_due_age_seconds: list[float] = []
    heartbeat_errors: list[BaseException] = []
    guard = threading.Lock()
    original_maintenance = maintenance.maintain_due_virtual_players

    heartbeat_stop = threading.Event()

    def _heartbeat_worker() -> None:
        close_old_connections()
        try:
            while not heartbeat_stop.is_set():
                try:
                    record_safety_heartbeat("safety_monitor", now=timezone.now())
                except BaseException as exc:  # pragma: no cover - asserted below
                    with guard:
                        heartbeat_errors.append(exc)
                    return
                heartbeat_stop.wait(timeout=5)
        finally:
            close_old_connections()

    def _instrumented_maintenance(*args, **kwargs):
        before_batch_planned_at = dict(
            BotProfile.objects.filter(pk__in=profile_ids).values_list("id", "last_planned_at")
        )
        oldest_due_at = (
            BotProfile.objects.filter(pk__in=profile_ids)
            .order_by("next_growth_at", "id")
            .values_list("next_growth_at", flat=True)
            .first()
        )
        assert oldest_due_at is not None
        oldest_due_age_seconds.append(max(0.0, (timezone.now() - oldest_due_at).total_seconds()))
        started = monotonic()
        with guard:
            owner_started_at.append(started)
        maintained = original_maintenance(*args, **kwargs)
        finished = monotonic()
        current_planned_at = dict(BotProfile.objects.filter(pk__in=profile_ids).values_list("id", "last_planned_at"))
        changed_profile_ids = {
            int(profile_id)
            for profile_id, last_planned_at in current_planned_at.items()
            if last_planned_at != before_batch_planned_at[int(profile_id)]
        }
        with guard:
            selected_batches.append(changed_profile_ids)
            owner_finished_at.append(finished)
        assert maintained == len(changed_profile_ids)
        return maintained

    monkeypatch.setattr(
        "gameplay.tasks.virtual_players.maintain_due_virtual_players",
        _instrumented_maintenance,
    )
    # This probe measures the timer-maintenance queue owner.  Completion
    # reconciliation and recruitment have independent capacity tests; running
    # them for every queued task would add unrelated full-table scans between
    # maintenance batches and make the 1000-profile envelope timeout on CI.
    monkeypatch.setattr(
        "gameplay.tasks.virtual_players.scan_virtual_player_maintenance_completions",
        lambda **_kwargs: (),
    )
    monkeypatch.setattr(
        "gameplay.tasks.virtual_players.schedule_due_virtual_recruitments",
        lambda **_kwargs: 0,
    )

    queue_name = settings.CELERY_TIMER_MAINTENANCE_QUEUE
    assert scan_virtual_player_maintenance_task.name == "gameplay.scan_virtual_player_maintenance"
    assert celery_app.conf.task_routes[scan_virtual_player_maintenance_task.name]["queue"] == queue_name

    dispatched_at: list[float] = []
    heartbeat_thread = threading.Thread(target=_heartbeat_worker, name="virtual-player-safety-heartbeat", daemon=True)
    with start_worker(
        celery_app,
        pool="solo",
        concurrency=1,
        loglevel="WARNING",
        perform_ping_check=False,
        shutdown_timeout=_WORKER_SHUTDOWN_TIMEOUT_SECONDS,
        queues=[queue_name],
    ):
        heartbeat_thread.start()
        try:
            results = []
            for _index in range(task_count):
                dispatched_at.append(monotonic())
                results.append(
                    scan_virtual_player_maintenance_task.apply_async(
                        kwargs={"limit": _TASK_BATCH_LIMIT},
                        queue=queue_name,
                    )
                )
            result_values = [result.get(timeout=600) for result in results]
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=_HEARTBEAT_JOIN_TIMEOUT_SECONDS)

    assert not heartbeat_thread.is_alive()
    assert heartbeat_errors == []

    assert all(isinstance(value, int) for value in result_values)
    assert len(owner_started_at) == task_count
    assert len(owner_finished_at) == task_count
    assert len(selected_batches) == task_count
    assert all(len(batch) <= _TASK_BATCH_LIMIT for batch in selected_batches)
    selected_profile_ids: set[int] = set()
    for batch in selected_batches:
        assert not selected_profile_ids.intersection(batch)
        selected_profile_ids.update(batch)
    assert selected_profile_ids == profile_ids

    first_dispatched_at = min(dispatched_at)
    queue_wait_ms = [(started - first_dispatched_at) * 1_000 for started in owner_started_at]
    owner_duration_ms = [
        (finished - started) * 1_000 for started, finished in zip(owner_started_at, owner_finished_at, strict=True)
    ]
    print(
        "virtual_player_celery_queue_capacity "
        f"tasks={task_count} profile_count={profile_count} "
        f"batch_limit={_TASK_BATCH_LIMIT} selected={[len(batch) for batch in selected_batches]} "
        f"queue_wait_ms={[round(value, 3) for value in queue_wait_ms]} "
        f"owner_duration_ms={[round(value, 3) for value in owner_duration_ms]} "
        f"queue_wait_max_ms={max(queue_wait_ms):.3f} "
        f"owner_duration_max_ms={max(owner_duration_ms):.3f} "
        f"oldest_due_age_max_seconds={max(oldest_due_age_seconds):.3f}"
    )

    assert max(queue_wait_ms) < _SCAN_PERIOD_SECONDS * 1_000 * 0.5
    assert max(owner_duration_ms) < _SCAN_PERIOD_SECONDS * 1_000 * 0.5
    assert max(oldest_due_age_seconds) < _SCAN_PERIOD_SECONDS * 2
