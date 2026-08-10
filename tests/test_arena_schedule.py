from __future__ import annotations

from django.conf import settings

from gameplay import tasks


def test_arena_scan_tasks_have_stable_exports_and_timer_routes():
    assert tasks.scan_arena_tournaments.name == "gameplay.scan_arena_tournaments"
    assert tasks.scan_arena_coop_events.name == "gameplay.scan_arena_coop_events"
    for task_name in [
        "gameplay.scan_arena_tournaments",
        "gameplay.scan_arena_coop_events",
    ]:
        assert settings.CELERY_TASK_ROUTES[task_name] == {"queue": settings.CELERY_TIMER_SCAN_QUEUE}


def test_arena_reserve_tasks_have_stable_exports_and_routes():
    assert tasks.reconcile_arena_virtual_reserve.name == "gameplay.reconcile_arena_virtual_reserve"
    assert tasks.scan_arena_virtual_reserves.name == "gameplay.scan_arena_virtual_reserves"
    assert tasks.grow_arena_virtual_reserves.name == "gameplay.grow_arena_virtual_reserves"
    assert tasks.retry_arena_shortage_metric.name == "gameplay.retry_arena_shortage_metric"
    assert (
        tasks.wake_active_arena_demands_for_population_region_task.name
        == "gameplay.wake_active_arena_demands_for_population_region"
    )
    for task_name in [
        "gameplay.reconcile_arena_virtual_reserve",
        "gameplay.wake_active_arena_demands_for_population_region",
        "gameplay.scan_arena_virtual_reserves",
        "gameplay.grow_arena_virtual_reserves",
        "gameplay.retry_arena_shortage_metric",
    ]:
        expected_queue = (
            settings.CELERY_TIMER_MAINTENANCE_QUEUE
            if task_name in {"gameplay.scan_arena_virtual_reserves", "gameplay.grow_arena_virtual_reserves"}
            else settings.CELERY_TIMER_QUEUE
        )
        assert settings.CELERY_TASK_ROUTES[task_name] == {"queue": expected_queue}


def test_arena_reserve_and_lifecycle_schedules_are_separate():
    reserve = settings.CELERY_BEAT_SCHEDULE["scan-arena-virtual-reserves"]
    tournament_entry = settings.CELERY_BEAT_SCHEDULE["scan-arena-tournaments"]
    coop_entry = settings.CELERY_BEAT_SCHEDULE["scan-arena-coop-events"]
    growth = settings.CELERY_BEAT_SCHEDULE["grow-arena-virtual-reserves"]

    assert reserve["task"] == "gameplay.scan_arena_virtual_reserves"
    assert tournament_entry["task"] == "gameplay.scan_arena_tournaments"
    assert coop_entry["task"] == "gameplay.scan_arena_coop_events"
    assert reserve["schedule"]._orig_minute == "*/5"
    assert tournament_entry["schedule"]._orig_minute == "*/1"
    assert coop_entry["schedule"]._orig_minute == "*/1"
    assert growth["task"] == "gameplay.grow_arena_virtual_reserves"
    assert growth["schedule"]._orig_minute == "1-59/5"
    assert growth["schedule"]._orig_hour == "*"
    assert reserve["schedule"].minute.isdisjoint(growth["schedule"].minute)
