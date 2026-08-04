"""
Gameplay tasks package.

This package contains all Celery tasks for the gameplay module, organized by domain:
- missions: Mission completion tasks
- buildings: Building upgrade tasks
- technology: Technology upgrade tasks
- production: All production tasks (horse, livestock, smelting, equipment, work)
- recruitment: Troop recruitment tasks
- pvp: Raid and scout tasks
- maintenance: Data cleanup and prisoner loyalty decay tasks
- global_mail: Global mail backfill tasks
"""

from __future__ import annotations

# Re-export commonly used imports for backward compatibility
from django.utils import timezone

from gameplay.models import MissionRun
from gameplay.services.manor.core import finalize_building_upgrade
from gameplay.services.technology import finalize_technology_upgrade

# Arena
from gameplay.tasks.arena import (
    grow_arena_virtual_reserves,
    reconcile_arena_virtual_reserve,
    retry_arena_shortage_metric,
    scan_arena_coop_events,
    scan_arena_tournaments,
    scan_arena_virtual_reserves,
)

# Buildings
from gameplay.tasks.buildings import complete_building_upgrade, scan_building_upgrades

# Global mail
from gameplay.tasks.global_mail import backfill_global_mail_campaign_task, enqueue_global_mail_backfill

# Maintenance
from gameplay.tasks.maintenance import cleanup_old_data_task, decay_prisoner_loyalty_task

# Missions
from gameplay.tasks.missions import complete_mission_task, scan_due_missions

# Production (horse, livestock, smelting, equipment, work)
from gameplay.tasks.production import (
    complete_equipment_forging,
    complete_horse_production,
    complete_livestock_production,
    complete_smelting_production,
    complete_work_assignments_task,
    scan_equipment_forgings,
    scan_horse_productions,
    scan_livestock_productions,
    scan_smelting_productions,
)

# PvP (raid, scout)
from gameplay.tasks.pvp import (
    complete_raid_task,
    complete_scout_return_task,
    complete_scout_task,
    process_raid_battle_task,
    scan_raid_runs,
    scan_scout_records,
)

# Recruitment
from gameplay.tasks.recruitment import complete_troop_recruitment, scan_troop_recruitments

# Resources
from gameplay.tasks.resources import sync_resource_production_task

# Technology
from gameplay.tasks.technology import complete_technology_upgrade, scan_technology_upgrades

# Virtual players
from gameplay.tasks.virtual_players import (
    aggregate_virtual_player_safety_task,
    cleanup_virtual_player_jail_task,
    cleanup_virtual_player_safety_metrics_task,
    heartbeat_virtual_player_arena_shortage_emitter_task,
    heartbeat_virtual_player_h01_callback_attempt_emitter_task,
    heartbeat_virtual_player_maintenance_attempt_emitter_task,
    monitor_virtual_player_safety_task,
    plan_virtual_players_task,
    reconcile_external_strength_reconciliation_task,
    reconcile_virtual_player_population_cell_task,
    roll_virtual_players_task,
    scan_external_strength_reconciliations_task,
    scan_virtual_player_population_demands_task,
)

# World chat
from gameplay.tasks.world_chat import (
    publish_world_chat_attempt_task,
    refund_world_chat_attempt_task,
    scan_world_chat_attempts_task,
)

__all__ = [
    # Backward compatibility
    "timezone",
    "MissionRun",
    "finalize_building_upgrade",
    "finalize_technology_upgrade",
    # Missions
    "complete_mission_task",
    "scan_due_missions",
    # Buildings
    "complete_building_upgrade",
    "scan_building_upgrades",
    # Arena
    "scan_arena_coop_events",
    "scan_arena_tournaments",
    "reconcile_arena_virtual_reserve",
    "retry_arena_shortage_metric",
    "scan_arena_virtual_reserves",
    "grow_arena_virtual_reserves",
    # Global mail
    "backfill_global_mail_campaign_task",
    "enqueue_global_mail_backfill",
    # Technology
    "complete_technology_upgrade",
    "scan_technology_upgrades",
    # Production
    "complete_horse_production",
    "scan_horse_productions",
    "complete_livestock_production",
    "scan_livestock_productions",
    "complete_smelting_production",
    "scan_smelting_productions",
    "complete_equipment_forging",
    "scan_equipment_forgings",
    "complete_work_assignments_task",
    # Resources
    "sync_resource_production_task",
    # Recruitment
    "complete_troop_recruitment",
    "scan_troop_recruitments",
    # PvP
    "complete_scout_task",
    "complete_scout_return_task",
    "scan_scout_records",
    "process_raid_battle_task",
    "complete_raid_task",
    "scan_raid_runs",
    # Maintenance
    "cleanup_old_data_task",
    "decay_prisoner_loyalty_task",
    # Virtual players
    "plan_virtual_players_task",
    "aggregate_virtual_player_safety_task",
    "cleanup_virtual_player_jail_task",
    "cleanup_virtual_player_safety_metrics_task",
    "heartbeat_virtual_player_arena_shortage_emitter_task",
    "heartbeat_virtual_player_h01_callback_attempt_emitter_task",
    "heartbeat_virtual_player_maintenance_attempt_emitter_task",
    "monitor_virtual_player_safety_task",
    "reconcile_external_strength_reconciliation_task",
    "reconcile_virtual_player_population_cell_task",
    "roll_virtual_players_task",
    "scan_external_strength_reconciliations_task",
    "scan_virtual_player_population_demands_task",
    # World chat
    "publish_world_chat_attempt_task",
    "refund_world_chat_attempt_task",
    "scan_world_chat_attempts_task",
]
