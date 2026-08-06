from __future__ import annotations

import pytest
from django.conf import settings

from config.settings.celery_conf import _validate_celery_queue_names


def test_additional_timer_driven_tasks_are_routed_to_timer_queue():
    expected_timer_tasks = [
        "core.record_celery_beat_heartbeat",
        "gameplay.complete_scout_return",
    ]

    for task_name in expected_timer_tasks:
        assert settings.CELERY_TASK_ROUTES[task_name] == {"queue": settings.CELERY_TIMER_QUEUE}


def test_batch_timer_tasks_are_routed_to_the_scan_queue():
    expected_scan_tasks = [
        "gameplay.scan_due_missions",
        "guests.scan_passive_hp_recovery",
        "guilds.scan_due_missions",
        "guilds.scan_due_raids",
    ]

    for task_name in expected_scan_tasks:
        assert settings.CELERY_TASK_ROUTES[task_name] == {"queue": settings.CELERY_TIMER_SCAN_QUEUE}


def test_long_running_maintenance_tasks_are_routed_to_the_maintenance_queue():
    expected_maintenance_tasks = [
        "gameplay.cleanup_old_data",
        "gameplay.scan_virtual_player_population_demands",
        "gameplay.scan_arena_virtual_reserves",
        "guilds.cleanup_old_logs",
        "trade.process_expired_listings",
        "trade.process_pending_auction_deliveries",
        "trade.settle_auction_round",
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
