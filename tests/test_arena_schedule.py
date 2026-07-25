from __future__ import annotations

from django.conf import settings

from gameplay import tasks


def test_arena_scan_tasks_have_stable_exports_and_timer_routes():
    assert tasks.scan_arena_tournaments.name == "gameplay.scan_arena_tournaments"
    assert tasks.scan_arena_coop_events.name == "gameplay.scan_arena_coop_events"
    for task_name in ["gameplay.scan_arena_tournaments", "gameplay.scan_arena_coop_events"]:
        assert settings.CELERY_TASK_ROUTES[task_name] == {"queue": settings.CELERY_TIMER_QUEUE}


def test_arena_and_coop_scans_are_scheduled_independently_every_minute():
    tournament_entry = settings.CELERY_BEAT_SCHEDULE["scan-arena-tournaments"]
    coop_entry = settings.CELERY_BEAT_SCHEDULE["scan-arena-coop-events"]

    assert tournament_entry["task"] == "gameplay.scan_arena_tournaments"
    assert coop_entry["task"] == "gameplay.scan_arena_coop_events"
    assert tournament_entry["schedule"]._orig_minute == "*/1"
    assert coop_entry["schedule"]._orig_minute == "*/1"
