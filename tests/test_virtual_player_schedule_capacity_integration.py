from __future__ import annotations

import math
from datetime import timedelta
from time import perf_counter

import pytest
from django.conf import settings
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from gameplay.models import BotProfile
from gameplay.services.virtual_player_core import maintenance
from gameplay.services.virtual_player_core.policy_registry import release_configured_policy_operation
from gameplay.services.virtual_player_core.safety_metrics import record_safety_heartbeat
from tests.test_virtual_player_maintenance_concurrency_integration import (
    _activate_v2_maintenance,
    _create_v2_profiles,
    _install_permissive_reference,
    _require_mysql,
)

pytestmark = [pytest.mark.integration, pytest.mark.capacity]

_SCAN_BATCH_LIMIT = 200
_PROFILE_SCAN_PERIOD = timedelta(hours=1)


def _nearest_rank(values: list[float], quantile: float) -> float:
    assert values
    return sorted(values)[math.ceil(len(values) * quantile) - 1]


def _write_query_count(captured_queries: list[dict[str, str]]) -> int:
    prefixes = ("INSERT ", "UPDATE ", "DELETE ", "REPLACE ")
    return sum(query["sql"].lstrip().upper().startswith(prefixes) for query in captured_queries)


def _row_lock_time_ms() -> float:
    with connection.cursor() as cursor:
        cursor.execute("SHOW SESSION STATUS LIKE 'Innodb_row_lock_time'")
        row = cursor.fetchone()
    if row is None:
        raise AssertionError("MySQL did not expose Innodb_row_lock_time")
    return float(row[1])


@pytest.fixture
def released_v2_policy(db):
    return release_configured_policy_operation(version=2, apply=True)


@pytest.mark.parametrize("profile_count", (100, 500, 1000))
@pytest.mark.django_db(transaction=True)
def test_same_time_due_capacity_matrix_is_bounded_and_fair(
    profile_count: int,
    released_v2_policy,
    game_data,
    monkeypatch,
) -> None:
    """Replay the documented 200/500/1000 due-profile capacity envelope.

    The test intentionally invokes the real scheduled-maintenance owner in a
    disposable MySQL test database.  It advances one scheduler tick at a
    time, so a profile receives at most one ordinary action per tick; this
    tests queue selection/fairness rather than draining a profile's 16-slot
    cycle in a tight loop.
    """

    _require_mysql()
    _activate_v2_maintenance()
    _install_permissive_reference(monkeypatch)

    schedule = settings.CELERY_BEAT_SCHEDULE["roll-virtual-players"]
    assert schedule["schedule"]._orig_hour == "*"
    assert schedule["schedule"]._orig_minute == 7

    base_now = timezone.now()
    profiles = _create_v2_profiles(
        count=profile_count,
        now=base_now,
        policy=released_v2_policy,
    )
    profile_ids = {int(profile.id) for profile in profiles}
    record_safety_heartbeat("safety_monitor", now=base_now)

    selected_profile_ids: set[int] = set()
    durations_ms: list[float] = []
    query_counts: list[int] = []
    write_query_counts: list[int] = []
    lock_waits_ms: list[float] = []
    oldest_due_ages_seconds: list[float] = []
    batch_counts: list[int] = []
    expected_batches = math.ceil(profile_count / _SCAN_BATCH_LIMIT)

    for tick in range(expected_batches):
        scan_now = base_now + timedelta(minutes=tick + 1)
        before_planned_at = dict(BotProfile.objects.filter(pk__in=profile_ids).values_list("id", "last_planned_at"))
        due_profile = (
            BotProfile.objects.filter(
                pk__in=profile_ids,
                next_growth_at__lte=scan_now,
            )
            .order_by("next_growth_at", "last_planned_at", "id")
            .first()
        )
        assert due_profile is not None
        oldest_due_age = max(0.0, (scan_now - due_profile.next_growth_at).total_seconds())

        record_safety_heartbeat("safety_monitor", now=scan_now)
        started = perf_counter()
        lock_wait_before = _row_lock_time_ms()
        with CaptureQueriesContext(connection) as captured:
            maintained = maintenance.maintain_due_virtual_players(
                now=scan_now,
                limit=_SCAN_BATCH_LIMIT,
            )
        lock_wait_after = _row_lock_time_ms()
        duration_ms = (perf_counter() - started) * 1_000

        committed_profile_ids = {
            int(profile_id)
            for profile_id, last_planned_at in BotProfile.objects.filter(pk__in=profile_ids).values_list(
                "id", "last_planned_at"
            )
            if last_planned_at != before_planned_at[int(profile_id)]
        }
        assert maintained == len(committed_profile_ids)
        assert len(committed_profile_ids) <= _SCAN_BATCH_LIMIT
        assert not selected_profile_ids.intersection(committed_profile_ids)
        selected_profile_ids.update(committed_profile_ids)

        batch_counts.append(len(committed_profile_ids))
        durations_ms.append(duration_ms)
        query_counts.append(len(captured.captured_queries))
        write_query_counts.append(_write_query_count(captured.captured_queries))
        lock_waits_ms.append(max(0.0, lock_wait_after - lock_wait_before))
        oldest_due_ages_seconds.append(oldest_due_age)

    duration_p95_ms = _nearest_rank(durations_ms, 0.95)
    duration_p99_ms = _nearest_rank(durations_ms, 0.99)
    lock_wait_p95_ms = _nearest_rank(lock_waits_ms, 0.95)
    lock_wait_p99_ms = _nearest_rank(lock_waits_ms, 0.99)
    print(
        "virtual_player_capacity_matrix "
        f"profile_count={profile_count} scan_batch_limit={_SCAN_BATCH_LIMIT} "
        f"batches={len(batch_counts)} selected={len(selected_profile_ids)} "
        f"batch_counts={batch_counts} "
        f"duration_p95_ms={duration_p95_ms:.3f} duration_p99_ms={duration_p99_ms:.3f} "
        f"queries_max={max(query_counts)} writes_max={max(write_query_counts)} "
        f"lock_wait_p95_ms={lock_wait_p95_ms:.3f} lock_wait_p99_ms={lock_wait_p99_ms:.3f} "
        f"oldest_due_age_max_seconds={max(oldest_due_ages_seconds):.3f} "
        f"scan_period_seconds={_PROFILE_SCAN_PERIOD.total_seconds():.0f} "
        "queue_wait_ms=0 direct_invocation=true"
    )

    assert selected_profile_ids == profile_ids
    assert batch_counts == [_SCAN_BATCH_LIMIT] * (expected_batches - 1) + [
        profile_count % _SCAN_BATCH_LIMIT or _SCAN_BATCH_LIMIT
    ]
    assert duration_p95_ms <= _PROFILE_SCAN_PERIOD.total_seconds() * 1_000 * 0.5
    assert duration_p99_ms <= _PROFILE_SCAN_PERIOD.total_seconds() * 1_000
    assert lock_wait_p95_ms <= 100
    assert lock_wait_p99_ms <= 1_000
    assert max(oldest_due_ages_seconds) < _PROFILE_SCAN_PERIOD.total_seconds() * 2
