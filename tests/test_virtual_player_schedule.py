from __future__ import annotations

from django.conf import settings


def test_virtual_player_timer_tasks_are_routed_to_timer_queue():
    expected_timer_tasks = [
        "gameplay.reconcile_external_strength_reconciliation",
    ]

    for task_name in expected_timer_tasks:
        assert settings.CELERY_TASK_ROUTES[task_name] == {"queue": settings.CELERY_TIMER_QUEUE}

    for task_name in [
        "gameplay.plan_virtual_players",
        "gameplay.roll_virtual_players",
        "gameplay.scan_virtual_player_maintenance",
        "gameplay.scan_external_strength_reconciliations",
    ]:
        assert settings.CELERY_TASK_ROUTES[task_name] == {"queue": settings.CELERY_TIMER_MAINTENANCE_QUEUE}


def test_virtual_player_planning_and_rolling_are_scheduled_separately():
    planning = settings.CELERY_BEAT_SCHEDULE["plan-virtual-players"]
    rolling = settings.CELERY_BEAT_SCHEDULE["roll-virtual-players"]

    assert planning["task"] == "gameplay.plan_virtual_players"
    assert planning["schedule"]._orig_hour == 4
    assert planning["schedule"]._orig_minute == 17
    assert rolling["task"] == "gameplay.roll_virtual_players"
    assert rolling["schedule"]._orig_minute == 7
    assert rolling["schedule"]._orig_hour == "*"

    maintenance_scan = settings.CELERY_BEAT_SCHEDULE["scan-virtual-player-maintenance"]
    assert maintenance_scan["task"] == "gameplay.scan_virtual_player_maintenance"
    assert maintenance_scan["schedule"]._orig_minute == "*"
    assert maintenance_scan["schedule"]._orig_hour == "*"


def test_population_demand_scan_runs_every_five_minutes():
    scan = settings.CELERY_BEAT_SCHEDULE["scan-virtual-player-population-demands"]

    assert scan["task"] == "gameplay.scan_virtual_player_population_demands"
    assert scan["schedule"]._orig_minute == "*/5"


def test_external_strength_reconciliation_tasks_are_exported_and_recover_every_five_minutes():
    from gameplay import tasks

    worker = tasks.reconcile_external_strength_reconciliation_task
    scanner = tasks.scan_external_strength_reconciliations_task
    schedule = settings.CELERY_BEAT_SCHEDULE["scan-external-strength-reconciliations"]

    assert worker.name == "gameplay.reconcile_external_strength_reconciliation"
    assert scanner.name == "gameplay.scan_external_strength_reconciliations"
    assert schedule["task"] == "gameplay.scan_external_strength_reconciliations"
    assert schedule["schedule"]._orig_minute == "*/5"
