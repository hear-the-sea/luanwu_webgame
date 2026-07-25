from __future__ import annotations

from django.conf import settings


def test_virtual_player_timer_tasks_are_routed_to_timer_queue():
    expected_timer_tasks = [
        "gameplay.plan_virtual_players",
        "gameplay.roll_virtual_players",
    ]

    for task_name in expected_timer_tasks:
        assert settings.CELERY_TASK_ROUTES[task_name] == {"queue": settings.CELERY_TIMER_QUEUE}


def test_virtual_player_planning_and_rolling_are_scheduled_separately():
    planning = settings.CELERY_BEAT_SCHEDULE["plan-virtual-players"]
    rolling = settings.CELERY_BEAT_SCHEDULE["roll-virtual-players"]

    assert planning["task"] == "gameplay.plan_virtual_players"
    assert planning["schedule"]._orig_hour == 4
    assert planning["schedule"]._orig_minute == 17
    assert rolling["task"] == "gameplay.roll_virtual_players"
    assert rolling["schedule"]._orig_minute == 7
    assert rolling["schedule"]._orig_hour == "*"
