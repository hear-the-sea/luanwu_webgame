from __future__ import annotations

import pytest
from django.conf import settings

from config.settings.celery_conf import _validate_celery_queue_names


def test_additional_timer_driven_tasks_are_routed_to_timer_queue():
    expected_timer_tasks = [
        "gameplay.complete_scout_return",
        "gameplay.wake_active_arena_demands_for_population_region",
    ]

    for task_name in expected_timer_tasks:
        assert settings.CELERY_TASK_ROUTES[task_name] == {"queue": settings.CELERY_TIMER_QUEUE}


def test_batch_timer_tasks_are_routed_to_the_scan_queue():
    expected_scan_tasks = [
        "gameplay.scan_due_missions",
        "guests.scan_passive_hp_recovery",
        "guests.cleanup_expired_recruitment_candidates",
        "gameplay.scan_arena_virtual_reserves",
        "gameplay.grow_arena_virtual_reserves",
        "gameplay.collect_arena_fill_service_outcomes",
        "gameplay.scan_world_chat_attempts",
        "trade.process_expired_listings",
        "trade.process_pending_auction_deliveries",
        "gameplay.scan_arena_wheel_events",
        "guilds.scan_due_missions",
        "guilds.scan_due_raids",
    ]

    for task_name in expected_scan_tasks:
        assert settings.CELERY_TASK_ROUTES[task_name] == {"queue": settings.CELERY_TIMER_SCAN_QUEUE}


def test_long_running_maintenance_tasks_are_routed_to_the_maintenance_queue():
    expected_maintenance_tasks = [
        "gameplay.cleanup_old_data",
        "gameplay.cleanup_expired_jail_prisoners",
        "gameplay.scan_virtual_player_population_demands",
        "gameplay.scan_virtual_player_maintenance",
        "guilds.cleanup_old_logs",
        "trade.settle_auction_round",
        "gameplay.cleanup_arena_wheel_events",
    ]

    for task_name in expected_maintenance_tasks:
        assert settings.CELERY_TASK_ROUTES[task_name] == {"queue": settings.CELERY_TIMER_MAINTENANCE_QUEUE}


def test_scan_and_maintenance_task_sets_do_not_overlap():
    assert settings.CELERY_TIMER_SCAN_TASKS.isdisjoint(settings.CELERY_TIMER_MAINTENANCE_TASKS)


def test_celery_queue_name_validation_rejects_empty_names():
    with pytest.raises(RuntimeError, match="non-empty"):
        _validate_celery_queue_names(("default", ""))


def test_celery_queue_name_validation_rejects_collisions():
    with pytest.raises(RuntimeError, match="unique"):
        _validate_celery_queue_names(("default", "timer", "timer"))


def test_route_map_is_generated_from_disjoint_queue_groups():
    expected_task_names = {
        task_name for _queue_name, task_names in settings.CELERY_TASK_QUEUE_GROUPS for task_name in task_names
    }

    assert set(settings.CELERY_TASK_ROUTES) == expected_task_names
    scheduled_task_names = {entry["task"] for entry in settings.CELERY_BEAT_SCHEDULE.values()}
    assert scheduled_task_names <= expected_task_names
    for queue_name, task_names in settings.CELERY_TASK_QUEUE_GROUPS:
        for task_name in task_names:
            assert settings.CELERY_TASK_ROUTES[task_name] == {"queue": queue_name}


def test_pending_auction_delivery_scan_is_scheduled():
    entry = settings.CELERY_BEAT_SCHEDULE["process-pending-auction-deliveries"]

    assert entry["task"] == "trade.process_pending_auction_deliveries"


def test_arena_wheel_scan_is_frequent_and_cleanup_is_maintenance():
    from celery.schedules import schedule

    scan_entry = settings.CELERY_BEAT_SCHEDULE["scan-arena-wheel-events"]
    cleanup_entry = settings.CELERY_BEAT_SCHEDULE["cleanup-arena-wheel-events"]

    assert scan_entry["task"] == "gameplay.scan_arena_wheel_events"
    assert schedule(scan_entry["schedule"]).run_every.total_seconds() == 10
    assert cleanup_entry["task"] == "gameplay.cleanup_arena_wheel_events"


def test_record_retention_tasks_run_hourly():
    cleanup_entry = settings.CELERY_BEAT_SCHEDULE["cleanup-old-resource-events"]
    guild_cleanup_entry = settings.CELERY_BEAT_SCHEDULE["cleanup-old-guild-logs"]
    safety_cleanup_entry = settings.CELERY_BEAT_SCHEDULE["cleanup-virtual-player-safety-metrics"]

    assert cleanup_entry["task"] == "gameplay.cleanup_old_data"
    assert guild_cleanup_entry["task"] == "guilds.cleanup_old_logs"
    assert cleanup_entry["schedule"].minute == {0}
    assert guild_cleanup_entry["schedule"].minute == {20}
    assert cleanup_entry["schedule"].hour == set(range(24))
    assert guild_cleanup_entry["schedule"].hour == set(range(24))
    assert safety_cleanup_entry["schedule"].minute == {40}
    assert safety_cleanup_entry["schedule"].hour == set(range(24))
    assert safety_cleanup_entry.get("args", ()) == ()
