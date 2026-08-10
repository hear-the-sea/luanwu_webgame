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
CELERY_TIMER_SCAN_QUEUE = env("CELERY_TIMER_SCAN_QUEUE", "timer_scan")
CELERY_TIMER_MAINTENANCE_QUEUE = env("CELERY_TIMER_MAINTENANCE_QUEUE", "timer_maintenance")
CELERY_TASK_DEFAULT_QUEUE = CELERY_DEFAULT_QUEUE


def _validate_celery_queue_names(queue_names: tuple[str, ...]) -> None:
    if any(not isinstance(queue_name, str) or not queue_name.strip() for queue_name in queue_names):
        raise RuntimeError("Celery queue names must be non-empty")
    if len(queue_names) != len(set(queue_names)):
        raise RuntimeError("Celery queue names must be unique")


_CELERY_QUEUE_NAMES = (
    CELERY_DEFAULT_QUEUE,
    CELERY_BATTLE_QUEUE,
    CELERY_TIMER_QUEUE,
    CELERY_TIMER_SCAN_QUEUE,
    CELERY_TIMER_MAINTENANCE_QUEUE,
)
_validate_celery_queue_names(_CELERY_QUEUE_NAMES)

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

CELERY_TASK_QUEUES = tuple(Queue(queue_name) for queue_name in _CELERY_QUEUE_NAMES)

# 批量扫描/维护任务与单记录完成任务分开消费，避免一个慢扫描阻塞到期状态推进。
CELERY_TIMER_SCAN_TASKS = frozenset(
    {
        "gameplay.scan_due_missions",
        "gameplay.scan_building_upgrades",
        "gameplay.scan_technology_upgrades",
        "gameplay.scan_troop_recruitments",
        "gameplay.scan_horse_productions",
        "gameplay.scan_livestock_productions",
        "gameplay.scan_smelting_productions",
        "gameplay.scan_equipment_forgings",
        "gameplay.complete_work_assignments",
        "gameplay.sync_resource_production",
        "guests.scan_training",
        "guests.scan_recruitments",
        "guests.scan_passive_hp_recovery",
        "guests.scan_injury_loyalty_decay",
        "gameplay.scan_scout_records",
        "gameplay.scan_arena_tournaments",
        "gameplay.scan_arena_coop_events",
        "gameplay.scan_raid_runs",
        "guilds.scan_due_raids",
        "guilds.scan_due_missions",
        "guilds.process_single_guild_production",
        "guilds.tech_daily_production",
    }
)

# Low-priority or potentially long-running maintenance is isolated so it cannot
# consume the small timer-scan worker pool needed for user-facing state scans.
CELERY_TIMER_MAINTENANCE_TASKS = frozenset(
    {
        "gameplay.scan_arena_virtual_reserves",
        "gameplay.grow_arena_virtual_reserves",
        "gameplay.plan_virtual_players",
        "gameplay.roll_virtual_players",
        "gameplay.scan_virtual_player_maintenance",
        "gameplay.scan_external_strength_reconciliations",
        "gameplay.scan_virtual_player_population_demands",
        "gameplay.scan_virtual_player_growth_control",
        "gameplay.aggregate_virtual_player_safety",
        "gameplay.monitor_virtual_player_safety",
        "gameplay.cleanup_virtual_player_safety_metrics",
        "gameplay.cleanup_virtual_player_jail",
        "gameplay.backfill_global_mail_campaign",
        "gameplay.cleanup_old_data",
        "gameplay.decay_prisoner_loyalty",
        "gameplay.scan_world_chat_attempts",
        "guilds.cleanup_invalid_hero_pool",
        "guilds.reset_weekly_stats",
        "guilds.cleanup_old_logs",
        "trade.refresh_shop_stock",
        "trade.process_expired_listings",
        "trade.process_pending_auction_deliveries",
        "trade.settle_auction_round",
        "trade.create_auction_round",
    }
)

# Keep every timer task in exactly one queue group. The route map is generated
# below so adding a task cannot silently leave a stale second definition behind.
CELERY_TIMER_DEFAULT_TASKS = frozenset(
    {
        "core.record_celery_beat_heartbeat",
        "gameplay.complete_mission",
        "gameplay.complete_building_upgrade",
        "gameplay.complete_technology_upgrade",
        "gameplay.complete_troop_recruitment",
        "gameplay.complete_horse_production",
        "gameplay.complete_livestock_production",
        "gameplay.complete_smelting_production",
        "gameplay.complete_equipment_forging",
        "guests.complete_training",
        "guests.complete_recruitment",
        "guests.process_daily_loyalty",
        "gameplay.complete_scout",
        "gameplay.complete_scout_return",
        "gameplay.reconcile_arena_virtual_reserve",
        "gameplay.wake_active_arena_demands_for_population_region",
        "gameplay.retry_arena_shortage_metric",
        "gameplay.process_raid_battle",
        "gameplay.complete_raid",
        "gameplay.reconcile_external_strength_reconciliation",
        "gameplay.reconcile_virtual_player_maintenance_completion",
        "gameplay.reconcile_virtual_player_population_cell",
        "gameplay.heartbeat_virtual_player_maintenance_attempt_emitter",
        "gameplay.heartbeat_virtual_player_h01_callback_attempt_emitter",
        "gameplay.heartbeat_virtual_player_arena_shortage_emitter",
        "gameplay.publish_world_chat_attempt",
        "gameplay.refund_world_chat_attempt",
        "guilds.complete_guild_mission",
        "guilds.complete_guild_raid",
    }
)

CELERY_TASK_QUEUE_GROUPS = (
    (CELERY_BATTLE_QUEUE, frozenset({"battle.generate_report"})),
    (CELERY_TIMER_QUEUE, CELERY_TIMER_DEFAULT_TASKS),
    (CELERY_TIMER_SCAN_QUEUE, CELERY_TIMER_SCAN_TASKS),
    (CELERY_TIMER_MAINTENANCE_QUEUE, CELERY_TIMER_MAINTENANCE_TASKS),
)
_CELERY_TASK_NAMES = [task_name for _queue_name, task_names in CELERY_TASK_QUEUE_GROUPS for task_name in task_names]
if len(_CELERY_TASK_NAMES) != len(set(_CELERY_TASK_NAMES)):
    raise RuntimeError("Celery task queue groups must be disjoint")

CELERY_TASK_ROUTES = {
    task_name: {"queue": queue_name} for queue_name, task_names in CELERY_TASK_QUEUE_GROUPS for task_name in task_names
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
    "scan-injury-loyalty-decay": {
        "task": "guests.scan_injury_loyalty_decay",
        "schedule": crontab(minute="*/1"),
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
    "sync-resource-production": {
        "task": "gameplay.sync_resource_production",
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
    "scan-arena-coop-events": {
        "task": "gameplay.scan_arena_coop_events",
        "schedule": crontab(minute="*/1"),
    },
    "scan-arena-virtual-reserves": {
        "task": "gameplay.scan_arena_virtual_reserves",
        "schedule": crontab(minute="*/5"),
    },
    "grow-arena-virtual-reserves": {
        "task": "gameplay.grow_arena_virtual_reserves",
        "schedule": crontab(minute="1-59/5"),
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
        "schedule": crontab(hour=4, minute=17),
    },
    "scan-virtual-player-growth-control": {
        "task": "gameplay.scan_virtual_player_growth_control",
        "schedule": crontab(hour=4, minute=5),
    },
    "roll-virtual-players": {
        "task": "gameplay.roll_virtual_players",
        "schedule": crontab(hour="*", minute=7),
    },
    "scan-virtual-player-maintenance": {
        "task": "gameplay.scan_virtual_player_maintenance",
        "schedule": crontab(minute="*"),
    },
    "scan-virtual-player-population-demands": {
        "task": "gameplay.scan_virtual_player_population_demands",
        "schedule": crontab(minute="*/5"),
    },
    "scan-external-strength-reconciliations": {
        "task": "gameplay.scan_external_strength_reconciliations",
        "schedule": crontab(minute="*/5"),
    },
    "heartbeat-virtual-player-maintenance-attempt-emitter": {
        "task": "gameplay.heartbeat_virtual_player_maintenance_attempt_emitter",
        "schedule": crontab(minute="*"),
    },
    "heartbeat-virtual-player-h01-callback-attempt-emitter": {
        "task": "gameplay.heartbeat_virtual_player_h01_callback_attempt_emitter",
        "schedule": crontab(minute="*"),
    },
    "heartbeat-virtual-player-arena-shortage-emitter": {
        "task": "gameplay.heartbeat_virtual_player_arena_shortage_emitter",
        "schedule": crontab(minute="*"),
    },
    "aggregate-virtual-player-safety": {
        "task": "gameplay.aggregate_virtual_player_safety",
        "schedule": crontab(minute="*"),
    },
    "monitor-virtual-player-safety": {
        "task": "gameplay.monitor_virtual_player_safety",
        "schedule": crontab(minute="*"),
    },
    "cleanup-virtual-player-safety-metrics": {
        "task": "gameplay.cleanup_virtual_player_safety_metrics",
        "schedule": crontab(hour=5, minute=43),
    },
    "cleanup-virtual-player-jail": {
        "task": "gameplay.cleanup_virtual_player_jail",
        "schedule": crontab(hour=0, minute=20),
    },
}
