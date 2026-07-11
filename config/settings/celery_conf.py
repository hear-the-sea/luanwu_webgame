"""
Celery configuration - queues, routes, and beat schedule.
"""

from __future__ import annotations

import os

from celery.schedules import crontab
from kombu import Queue

from .base import env
from .database import REDIS_BROKER_URL, REDIS_PASSWORD, REDIS_RESULT_URL, _redis_url_with_password

CELERY_BROKER_URL = _redis_url_with_password(env("CELERY_BROKER_URL", REDIS_BROKER_URL), REDIS_PASSWORD)
CELERY_RESULT_BACKEND = env(
    "CELERY_RESULT_BACKEND",
    CELERY_BROKER_URL if "CELERY_BROKER_URL" in os.environ else REDIS_RESULT_URL,
)
CELERY_RESULT_BACKEND = _redis_url_with_password(CELERY_RESULT_BACKEND, REDIS_PASSWORD)
CELERY_RESULT_EXPIRES = int(env("CELERY_RESULT_EXPIRES", "3600"))
CELERY_TASK_STORE_EAGER_RESULT = False

CELERY_DEFAULT_QUEUE = env("CELERY_DEFAULT_QUEUE", "default")
CELERY_BATTLE_QUEUE = env("CELERY_BATTLE_QUEUE", "battle")
CELERY_TIMER_QUEUE = env("CELERY_TIMER_QUEUE", "timer")
CELERY_TASK_DEFAULT_QUEUE = CELERY_DEFAULT_QUEUE

HEALTH_CHECK_CELERY_WORKERS = (
    env(
        "DJANGO_HEALTH_CHECK_CELERY_WORKERS",
        "0",
    )
    == "1"
)
HEALTH_CHECK_CELERY_BEAT = (
    env(
        "DJANGO_HEALTH_CHECK_CELERY_BEAT",
        "0",
    )
    == "1"
)
HEALTH_CHECK_CELERY_ROUNDTRIP = (
    env(
        "DJANGO_HEALTH_CHECK_CELERY_ROUNDTRIP",
        "0",
    )
    == "1"
)
HEALTH_CHECK_CELERY_BEAT_MAX_AGE_SECONDS = int(env("DJANGO_HEALTH_CHECK_CELERY_BEAT_MAX_AGE_SECONDS", "180"))
HEALTH_CHECK_CELERY_ROUNDTRIP_TIMEOUT_SECONDS = float(env("DJANGO_HEALTH_CHECK_CELERY_ROUNDTRIP_TIMEOUT_SECONDS", "3"))

CELERY_TASK_QUEUES = (
    Queue(CELERY_DEFAULT_QUEUE),
    Queue(CELERY_BATTLE_QUEUE),
    Queue(CELERY_TIMER_QUEUE),
)

CELERY_TASK_ROUTES = {
    "battle.generate_report": {"queue": CELERY_BATTLE_QUEUE},
    "core.record_celery_beat_heartbeat": {"queue": CELERY_TIMER_QUEUE},
    "gameplay.complete_mission": {"queue": CELERY_TIMER_QUEUE},
    "gameplay.scan_due_missions": {"queue": CELERY_TIMER_QUEUE},
    "gameplay.complete_building_upgrade": {"queue": CELERY_TIMER_QUEUE},
    "gameplay.scan_building_upgrades": {"queue": CELERY_TIMER_QUEUE},
    "gameplay.complete_technology_upgrade": {"queue": CELERY_TIMER_QUEUE},
    "gameplay.scan_technology_upgrades": {"queue": CELERY_TIMER_QUEUE},
    "gameplay.complete_troop_recruitment": {"queue": CELERY_TIMER_QUEUE},
    "gameplay.scan_troop_recruitments": {"queue": CELERY_TIMER_QUEUE},
    "gameplay.complete_horse_production": {"queue": CELERY_TIMER_QUEUE},
    "gameplay.scan_horse_productions": {"queue": CELERY_TIMER_QUEUE},
    "gameplay.complete_livestock_production": {"queue": CELERY_TIMER_QUEUE},
    "gameplay.scan_livestock_productions": {"queue": CELERY_TIMER_QUEUE},
    "gameplay.complete_smelting_production": {"queue": CELERY_TIMER_QUEUE},
    "gameplay.scan_smelting_productions": {"queue": CELERY_TIMER_QUEUE},
    "gameplay.complete_equipment_forging": {"queue": CELERY_TIMER_QUEUE},
    "gameplay.scan_equipment_forgings": {"queue": CELERY_TIMER_QUEUE},
    "gameplay.complete_work_assignments": {"queue": CELERY_TIMER_QUEUE},
    "guests.complete_training": {"queue": CELERY_TIMER_QUEUE},
    "guests.scan_training": {"queue": CELERY_TIMER_QUEUE},
    "guests.complete_recruitment": {"queue": CELERY_TIMER_QUEUE},
    "guests.scan_recruitments": {"queue": CELERY_TIMER_QUEUE},
    "guests.scan_passive_hp_recovery": {"queue": CELERY_TIMER_QUEUE},
    "guests.process_daily_loyalty": {"queue": CELERY_TIMER_QUEUE},
    "gameplay.complete_scout": {"queue": CELERY_TIMER_QUEUE},
    "gameplay.complete_scout_return": {"queue": CELERY_TIMER_QUEUE},
    "gameplay.scan_scout_records": {"queue": CELERY_TIMER_QUEUE},
    "gameplay.scan_arena_tournaments": {"queue": CELERY_TIMER_QUEUE},
    "gameplay.process_raid_battle": {"queue": CELERY_TIMER_QUEUE},
    "gameplay.complete_raid": {"queue": CELERY_TIMER_QUEUE},
    "gameplay.scan_raid_runs": {"queue": CELERY_TIMER_QUEUE},
    "gameplay.plan_virtual_players": {"queue": CELERY_TIMER_QUEUE},
    "gameplay.roll_virtual_players": {"queue": CELERY_TIMER_QUEUE},
    "gameplay.backfill_global_mail_campaign": {"queue": CELERY_TIMER_QUEUE},
    "gameplay.cleanup_old_data": {"queue": CELERY_TIMER_QUEUE},
    "gameplay.decay_prisoner_loyalty": {"queue": CELERY_TIMER_QUEUE},
    "gameplay.publish_world_chat_attempt": {"queue": CELERY_TIMER_QUEUE},
    "gameplay.refund_world_chat_attempt": {"queue": CELERY_TIMER_QUEUE},
    "gameplay.scan_world_chat_attempts": {"queue": CELERY_TIMER_QUEUE},
    "guilds.complete_guild_mission": {"queue": CELERY_TIMER_QUEUE},
    "guilds.cleanup_invalid_hero_pool": {"queue": CELERY_TIMER_QUEUE},
    "guilds.complete_guild_raid": {"queue": CELERY_TIMER_QUEUE},
    "guilds.scan_due_raids": {"queue": CELERY_TIMER_QUEUE},
    "guilds.scan_due_missions": {"queue": CELERY_TIMER_QUEUE},
    "guilds.process_single_guild_production": {"queue": CELERY_TIMER_QUEUE},
    "guilds.tech_daily_production": {"queue": CELERY_TIMER_QUEUE},
    "guilds.reset_weekly_stats": {"queue": CELERY_TIMER_QUEUE},
    "guilds.cleanup_old_logs": {"queue": CELERY_TIMER_QUEUE},
    "trade.refresh_shop_stock": {"queue": CELERY_TIMER_QUEUE},
    "trade.process_expired_listings": {"queue": CELERY_TIMER_QUEUE},
    "trade.process_pending_auction_deliveries": {"queue": CELERY_TIMER_QUEUE},
    "trade.settle_auction_round": {"queue": CELERY_TIMER_QUEUE},
    "trade.create_auction_round": {"queue": CELERY_TIMER_QUEUE},
}

CELERY_BEAT_SCHEDULE = {
    "scan-building-upgrades": {
        "task": "gameplay.scan_building_upgrades",
        "schedule": crontab(minute="*/10"),
    },
    "scan-due-missions": {
        "task": "gameplay.scan_due_missions",
        "schedule": crontab(minute="*/1"),
    },
    "scan-technology-upgrades": {
        "task": "gameplay.scan_technology_upgrades",
        "schedule": crontab(minute="*/5"),
    },
    "scan-guest-training": {
        "task": "guests.scan_training",
        "schedule": crontab(minute="*/10"),
    },
    "scan-guest-recruitments": {
        "task": "guests.scan_recruitments",
        "schedule": crontab(minute="*/5"),
    },
    "scan-passive-guest-hp-recovery": {
        "task": "guests.scan_passive_hp_recovery",
        "schedule": crontab(minute="*/5"),
    },
    "process-daily-guest-loyalty": {
        "task": "guests.process_daily_loyalty",
        "schedule": crontab(hour=0, minute=0),
    },
    "scan-troop-recruitments": {
        "task": "gameplay.scan_troop_recruitments",
        "schedule": crontab(minute="*/5"),
    },
    "scan-horse-productions": {
        "task": "gameplay.scan_horse_productions",
        "schedule": crontab(minute="*/5"),
    },
    "scan-livestock-productions": {
        "task": "gameplay.scan_livestock_productions",
        "schedule": crontab(minute="*/5"),
    },
    "scan-smelting-productions": {
        "task": "gameplay.scan_smelting_productions",
        "schedule": crontab(minute="*/5"),
    },
    "scan-equipment-forgings": {
        "task": "gameplay.scan_equipment_forgings",
        "schedule": crontab(minute="*/5"),
    },
    "complete-work-assignments": {
        "task": "gameplay.complete_work_assignments",
        "schedule": crontab(minute="*/1"),
    },
    "refresh-shop-stock": {
        "task": "trade.refresh_shop_stock",
        "schedule": crontab(hour=0, minute=0),
    },
    "guild-tech-daily-production": {
        "task": "guilds.tech_daily_production",
        "schedule": crontab(hour=0, minute=0),
    },
    "reset-guild-weekly-stats": {
        "task": "guilds.reset_weekly_stats",
        "schedule": crontab(hour=0, minute=0, day_of_week=1),
    },
    "cleanup-old-guild-logs": {
        "task": "guilds.cleanup_old_logs",
        "schedule": crontab(hour=3, minute=0),
    },
    "cleanup-invalid-guild-hero-pool": {
        "task": "guilds.cleanup_invalid_hero_pool",
        "schedule": crontab(minute="*/5"),
    },
    "scan-due-guild-missions": {
        "task": "guilds.scan_due_missions",
        "schedule": crontab(minute="*/1"),
    },
    "scan-due-guild-raids": {
        "task": "guilds.scan_due_raids",
        "schedule": crontab(minute="*/1"),
    },
    "scan-scout-records": {
        "task": "gameplay.scan_scout_records",
        "schedule": crontab(minute="*/5"),
    },
    "scan-raid-runs": {
        "task": "gameplay.scan_raid_runs",
        "schedule": crontab(minute="*/5"),
    },
    "scan-arena-tournaments": {
        "task": "gameplay.scan_arena_tournaments",
        "schedule": crontab(minute="*/1"),
    },
    "process-expired-market-listings": {
        "task": "trade.process_expired_listings",
        "schedule": crontab(minute="*/2"),
    },
    "process-pending-auction-deliveries": {
        "task": "trade.process_pending_auction_deliveries",
        "schedule": crontab(minute="*/1"),
    },
    "cleanup-old-resource-events": {
        "task": "gameplay.cleanup_old_data",
        "schedule": crontab(hour=4, minute=0),
    },
    "decay-prisoner-loyalty": {
        "task": "gameplay.decay_prisoner_loyalty",
        "schedule": crontab(hour=0, minute=0),
    },
    "scan-world-chat-attempts": {
        "task": "gameplay.scan_world_chat_attempts",
        "schedule": crontab(minute="*"),
    },
    "settle-auction-round": {
        "task": "trade.settle_auction_round",
        # 更及时地结算到期轮次，避免“拍卖已结束但长时间未到账”的体验问题。
        "schedule": crontab(minute="*/5"),
    },
    "check-create-auction-round": {
        "task": "trade.create_auction_round",
        "schedule": crontab(hour=0, minute=10),
    },
    "record-celery-beat-heartbeat": {
        "task": "core.record_celery_beat_heartbeat",
        "schedule": crontab(minute="*"),
    },
    "plan-virtual-players": {
        "task": "gameplay.plan_virtual_players",
        "schedule": crontab(hour="3-5", minute=17),
    },
    "roll-virtual-players": {
        "task": "gameplay.roll_virtual_players",
        "schedule": crontab(minute="*/15"),
    },
}
