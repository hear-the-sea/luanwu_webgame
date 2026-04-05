from __future__ import annotations

import importlib

import pytest
from django.conf import settings
from django.core.management import call_command
from django.db.migrations.exceptions import IrreversibleError

from guilds.models import GuildMissionTemplate


def test_scan_due_guild_missions_is_routed_to_timer_queue():
    assert settings.CELERY_TASK_ROUTES["guilds.scan_due_missions"] == {"queue": settings.CELERY_TIMER_QUEUE}


def test_scan_due_guild_missions_is_scheduled_every_minute():
    entry = settings.CELERY_BEAT_SCHEDULE["scan-due-guild-missions"]

    assert entry["task"] == "guilds.scan_due_missions"
    assert entry["schedule"]._orig_minute == "*/1"


def test_guild_timer_tasks_are_routed_to_timer_queue():
    expected_timer_tasks = [
        "guilds.complete_guild_mission",
        "guilds.scan_due_missions",
        "guilds.complete_guild_raid",
        "guilds.scan_due_raids",
        "guilds.process_single_guild_production",
        "guilds.tech_daily_production",
        "guilds.reset_weekly_stats",
        "guilds.cleanup_old_logs",
        "guilds.cleanup_invalid_hero_pool",
    ]

    for task_name in expected_timer_tasks:
        assert settings.CELERY_TASK_ROUTES[task_name] == {"queue": settings.CELERY_TIMER_QUEUE}


def test_scan_due_guild_raids_is_scheduled_every_minute():
    entry = settings.CELERY_BEAT_SCHEDULE["scan-due-guild-raids"]

    assert entry["task"] == "guilds.scan_due_raids"
    assert entry["schedule"]._orig_minute == "*/1"


@pytest.mark.django_db
def test_guild_mission_migration_seed_uses_safe_inactive_defaults():
    migration = importlib.import_module("guilds.migrations.0008_guild_mission_and_troop_models")
    GuildMissionTemplate.objects.filter(key__in=["guild_patrol_alpha", "guild_supply_escort"]).delete()

    migration.seed_guild_mission_templates(apps=importlib.import_module("django.apps").apps, schema_editor=None)

    patrol = GuildMissionTemplate.objects.get(key="guild_patrol_alpha")
    escort = GuildMissionTemplate.objects.get(key="guild_supply_escort")

    assert patrol.is_active is False
    assert escort.is_active is False
    assert patrol.enemy_guests == []
    assert escort.enemy_guests == []
    assert patrol.task_type == "patrol"
    assert escort.task_type == "escort"


@pytest.mark.django_db
def test_guild_mission_task_type_alignment_migration_maps_legacy_values():
    migration = importlib.import_module("guilds.migrations.0013_align_guild_mission_task_types")

    legacy_dispatch_guest = GuildMissionTemplate.objects.create(
        key="legacy_dispatch_task_type",
        name="旧门客任务",
        difficulty="junior",
        task_type="dispatch",
        allow_troops=False,
    )
    legacy_patrol_troop = GuildMissionTemplate.objects.create(
        key="legacy_patrol_task_type",
        name="旧护院任务",
        difficulty="junior",
        task_type="patrol",
        allow_troops=True,
    )
    legacy_escort = GuildMissionTemplate.objects.create(
        key="legacy_escort_task_type",
        name="旧押运任务",
        difficulty="intermediate",
        task_type="escort",
    )
    legacy_suppress = GuildMissionTemplate.objects.create(
        key="legacy_suppress_task_type",
        name="旧讨伐任务",
        difficulty="advanced",
        task_type="suppress",
    )

    migration.migrate_guild_mission_task_types(apps=importlib.import_module("django.apps").apps, schema_editor=None)

    legacy_dispatch_guest.refresh_from_db()
    legacy_patrol_troop.refresh_from_db()
    legacy_escort.refresh_from_db()
    legacy_suppress.refresh_from_db()

    assert legacy_dispatch_guest.task_type == "guest"
    assert legacy_patrol_troop.task_type == "troop"
    assert legacy_escort.task_type == "troop"
    assert legacy_suppress.task_type == "defense"


@pytest.mark.django_db
def test_guild_mission_task_type_alignment_migration_is_explicitly_irreversible():
    migration = importlib.import_module("guilds.migrations.0013_align_guild_mission_task_types")

    with pytest.raises(IrreversibleError, match="lossy task_type migration"):
        migration.rollback_guild_mission_task_types(
            apps=importlib.import_module("django.apps").apps,
            schema_editor=None,
        )


@pytest.mark.django_db
def test_load_guild_mission_templates_default_data_uses_guild_special_enemy_keys():
    payload_path = settings.BASE_DIR / "data" / "guild_mission_templates.yaml"

    def _enemy_guest_key(entry):
        if isinstance(entry, str):
            return entry
        return entry.get("template_key") or entry.get("key") or ""

    call_command("load_guild_mission_templates", file=str(payload_path), verbosity=0)

    patrol = GuildMissionTemplate.objects.get(key="guild_patrol_alpha")
    escort = GuildMissionTemplate.objects.get(key="guild_supply_escort")
    advanced = GuildMissionTemplate.objects.get(key="guild_blackwind_assault")

    patrol_keys = [_enemy_guest_key(entry) for entry in patrol.enemy_guests]
    escort_keys = [_enemy_guest_key(entry) for entry in escort.enemy_guests]
    advanced_keys = [_enemy_guest_key(entry) for entry in advanced.enemy_guests]

    assert patrol_keys
    assert escort_keys
    assert advanced_keys
    assert all(key.startswith("guild_") for key in patrol_keys)
    assert all(key.startswith("guild_") for key in escort_keys)
    assert all(key.startswith("guild_") for key in advanced_keys)
    assert all(key.startswith("guild_wulinzhai_") for key in patrol_keys)
    assert all(key.startswith("guild_bloodflag_") for key in escort_keys)
    assert all(key.startswith("guild_blackwind_") for key in advanced_keys)
    assert patrol.task_type == "troop"
    assert escort.task_type == "troop"
    assert advanced.task_type == "troop"
