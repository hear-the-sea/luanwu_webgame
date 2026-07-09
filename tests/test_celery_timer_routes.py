from __future__ import annotations

from django.conf import settings


def test_additional_timer_driven_tasks_are_routed_to_timer_queue():
    expected_timer_tasks = [
        "core.record_celery_beat_heartbeat",
        "gameplay.cleanup_old_data",
        "gameplay.complete_scout_return",
        "gameplay.decay_prisoner_loyalty",
        "trade.create_auction_round",
        "trade.process_pending_auction_deliveries",
        "trade.process_expired_listings",
        "trade.refresh_shop_stock",
        "trade.settle_auction_round",
    ]

    for task_name in expected_timer_tasks:
        assert settings.CELERY_TASK_ROUTES[task_name] == {"queue": settings.CELERY_TIMER_QUEUE}


def test_pending_auction_delivery_scan_is_scheduled():
    entry = settings.CELERY_BEAT_SCHEDULE["process-pending-auction-deliveries"]

    assert entry["task"] == "trade.process_pending_auction_deliveries"
