from __future__ import annotations

import importlib

import pytest
from django.conf import settings

from guilds.models import GuildMissionTemplate


def test_scan_due_guild_missions_is_routed_to_timer_queue():
    assert settings.CELERY_TASK_ROUTES["guilds.scan_due_missions"] == {"queue": settings.CELERY_TIMER_QUEUE}


def test_scan_due_guild_missions_is_scheduled_every_minute():
    entry = settings.CELERY_BEAT_SCHEDULE["scan-due-guild-missions"]

    assert entry["task"] == "guilds.scan_due_missions"
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
