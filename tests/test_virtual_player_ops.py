from __future__ import annotations

import ast
from datetime import timedelta
from io import StringIO
from pathlib import Path

import pytest
from django.contrib import admin
from django.contrib.admin.sites import AdminSite
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from gameplay.services.manor.core import ensure_manor


def test_generate_virtual_players_command_uses_public_service_api():
    command_path = Path("gameplay/management/commands/generate_virtual_players.py")
    tree = ast.parse(command_path.read_text(encoding="utf-8"))
    private_imports = {"_prestige_bands", "_projection_for_band", "_weighted_archetype"}

    imported_private_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "gameplay.services.virtual_players"
        for alias in node.names
        if alias.name in private_imports
    }

    assert imported_private_names == set()


def test_virtual_player_yaml_is_registered_and_validates_bad_values(tmp_path):
    from core.utils.yaml_validators.registry import get_supported_yaml_configs, validate_all_configs

    for filename in get_supported_yaml_configs():
        (tmp_path / filename).write_text("{}\n", encoding="utf-8")

    (tmp_path / "virtual_players.yaml").write_text(
        """
enabled: yes
population:
  active_player_multiplier: -1
  min_per_region: bad
prestige_bands:
  newbie: [500]
lifecycle:
  active_days: [90, 30]
resources:
  balanced: [0.9, 0.1]
projection:
  guest_template_keys: bad
  extra_skill_keys: bad
  extra_skills_per_guest: [2]
  high_tier_skill_keys: bad
  high_tier_skill_chance: 2
  low_stage_powerful_item_chance: 2
  high_tier_skills_per_guest: [1]
  gear_slots_by_archetype: bad
  inventory_quantity_multipliers: bad
  loot_limits:
    real_attacker_daily_resource_cap: bad
""",
        encoding="utf-8",
    )

    assert "virtual_players.yaml" in get_supported_yaml_configs()
    result = validate_all_configs(tmp_path)
    messages = [str(error) for error in result.errors]
    assert any("active_player_multiplier" in message for message in messages)
    assert any("min_per_region" in message for message in messages)
    assert any("prestige_bands.newbie" in message for message in messages)
    assert any("resources.balanced" in message for message in messages)
    assert any("guest_template_keys" in message for message in messages)
    assert any("extra_skill_keys" in message for message in messages)
    assert any("extra_skills_per_guest" in message for message in messages)
    assert any("high_tier_skill_keys" in message for message in messages)
    assert any("high_tier_skill_chance" in message for message in messages)
    assert any("low_stage_powerful_item_chance" in message for message in messages)
    assert any("high_tier_skills_per_guest" in message for message in messages)
    assert any("gear_slots_by_archetype" in message for message in messages)
    assert any("inventory_quantity_multipliers" in message for message in messages)
    assert any("real_attacker_daily_resource_cap" in message for message in messages)


def test_virtual_player_default_skill_and_gear_keys_exist_in_source_configs():
    from core.utils.yaml_loader import load_yaml_data

    config = load_yaml_data(
        Path("data/virtual_players.yaml"),
        logger=None,
        context="virtual players config",
        default={},
    )
    projection = config["projection"]

    guest_skills = load_yaml_data(
        Path("data/guest_skills.yaml"),
        logger=None,
        context="guest skills config",
        default={},
    )
    skill_keys = {row["key"] for row in guest_skills.get("skills") or []}
    assert set(projection["extra_skill_keys"]) <= skill_keys
    assert set(projection["high_tier_skill_keys"]) <= skill_keys

    assert projection["guest_template_keys"] == "__all__"
    assert projection["gear_template_keys"] == "__all__"
    assert projection["troop_template_keys"] == "__all__"
    assert projection["technology_keys"] == "__all__"
    assert projection["item_template_keys"] == "__all_tradeable__"
    assert projection["powerful_item_min_growth_stage"] >= 1


@override_settings(VIRTUAL_PLAYER_CONFIG={"enabled": False})
def test_runtime_reload_refreshes_virtual_player_config_summary():
    from gameplay.services.runtime_configs import format_runtime_config_summary, reload_runtime_configs
    from gameplay.services.virtual_players import clear_virtual_player_config_cache

    clear_virtual_player_config_cache()
    summary = reload_runtime_configs()

    assert "virtual_players" in summary
    assert "virtual_players=" in format_runtime_config_summary(summary)


def test_bot_profile_is_registered_in_admin():
    from gameplay.models import BotProfile

    assert BotProfile in admin.site._registry
    model_admin = admin.site._registry[BotProfile]
    assert "manor_region" in model_admin.list_display
    assert "manor_prestige" in model_admin.list_display
    assert "loot_budget_daily" in model_admin.list_display
    assert "last_planned_at" in model_admin.list_display
    assert "maintenance_stopped_at" in model_admin.list_display
    assert "is_due_for_maintenance" in model_admin.list_display
    assert "state" in model_admin.list_filter
    assert "archetype" in model_admin.list_filter
    assert "target_prestige_band" in model_admin.list_filter
    assert "current_prestige_band" in model_admin.list_filter
    assert ("next_growth_at", admin.DateFieldListFilter) in model_admin.list_filter
    assert any(getattr(item, "parameter_name", None) == "due_maintenance" for item in model_admin.list_filter)
    assert "manor__name" in model_admin.search_fields
    assert "manor__user__username" in model_admin.search_fields
    assert "mark_selected_stale" in model_admin.actions
    assert "growth_seed" in model_admin.readonly_fields
    assert "prestige_band" in model_admin.readonly_fields
    assert "target_prestige_band" in model_admin.readonly_fields
    assert "current_prestige_band" in model_admin.readonly_fields
    assert "last_planned_at" in model_admin.readonly_fields
    assert "maintenance_stopped_at" in model_admin.readonly_fields


def test_bot_inventory_daily_counter_is_registered_readonly_in_admin():
    from gameplay.admin import BotInventoryDailyCounterAdmin
    from gameplay.models import BotInventoryDailyCounter

    assert BotInventoryDailyCounter in admin.site._registry
    model_admin = admin.site._registry[BotInventoryDailyCounter]
    assert isinstance(model_admin, BotInventoryDailyCounterAdmin)
    assert model_admin.list_display == ("counter_date", "category", "quantity", "updated_at")
    assert model_admin.list_filter == ("category", ("counter_date", admin.DateFieldListFilter))
    assert set(model_admin.readonly_fields) == {"category", "counter_date", "quantity", "created_at", "updated_at"}


def test_bot_backfill_demand_is_registered_readonly_in_admin():
    from gameplay.admin import BotBackfillDemandAdmin
    from gameplay.models import BotBackfillDemand

    assert BotBackfillDemand in admin.site._registry
    model_admin = admin.site._registry[BotBackfillDemand]
    assert isinstance(model_admin, BotBackfillDemandAdmin)
    assert model_admin.list_display == ("region", "prestige_band", "needed", "updated_at")
    assert model_admin.list_filter == ("region", "prestige_band")
    assert set(model_admin.readonly_fields) == {"region", "prestige_band", "needed", "created_at", "updated_at"}


def _create_bot_profile(django_user_model, *, state="active", next_growth_at=None):
    from gameplay.models import BotProfile

    user = django_user_model.objects.create_user(username=f"bot_admin_{timezone.now().timestamp()}", password="pass123")
    manor = ensure_manor(user)
    manor.region = "north"
    manor.prestige = 1200
    manor.save(update_fields=["region", "prestige"])
    now = timezone.now()
    return BotProfile.objects.create(
        manor=manor,
        archetype=BotProfile.Archetype.BALANCED,
        state=state,
        prestige_band="junior",
        growth_seed=123,
        growth_stage=3,
        next_growth_at=next_growth_at or now + timedelta(hours=1),
        abandon_at=now + timedelta(days=30),
        retire_at=now + timedelta(days=60),
        loot_budget_daily=1000,
        last_planned_at=now - timedelta(hours=1),
    )


@pytest.mark.django_db
def test_bot_profile_admin_mark_selected_stale_action(django_user_model, monkeypatch):
    from gameplay.admin.bots import BotProfileAdmin
    from gameplay.models import BotProfile

    profile = _create_bot_profile(django_user_model)
    retired = _create_bot_profile(django_user_model, state=BotProfile.State.RETIRED)
    admin_obj = BotProfileAdmin(BotProfile, AdminSite())
    messages = []
    monkeypatch.setattr(admin_obj, "message_user", lambda _request, message, **_kwargs: messages.append(str(message)))

    admin_obj.mark_selected_stale(None, BotProfile.objects.filter(id__in=[profile.id, retired.id]))

    profile.refresh_from_db()
    retired.refresh_from_db()
    assert profile.state == BotProfile.State.STALE
    assert profile.next_growth_at <= timezone.now()
    assert retired.state == BotProfile.State.RETIRED
    assert messages and "1" in messages[0]


@pytest.mark.django_db
def test_bot_profile_admin_due_display_excludes_retired(django_user_model):
    from gameplay.admin.bots import BotProfileAdmin
    from gameplay.models import BotProfile

    admin_obj = BotProfileAdmin(BotProfile, AdminSite())
    due = _create_bot_profile(django_user_model, next_growth_at=timezone.now() - timedelta(minutes=1))
    future = _create_bot_profile(django_user_model, next_growth_at=timezone.now() + timedelta(hours=1))
    retired = _create_bot_profile(
        django_user_model,
        state=BotProfile.State.RETIRED,
        next_growth_at=timezone.now() - timedelta(minutes=1),
    )

    assert admin_obj.is_due_for_maintenance(due) is True
    assert admin_obj.is_due_for_maintenance(future) is False
    assert admin_obj.is_due_for_maintenance(retired) is False


@pytest.mark.django_db
def test_generate_virtual_players_dry_run_does_not_create_rows(settings):
    from gameplay.models import BotProfile

    settings.VIRTUAL_PLAYER_CONFIG = {
        "prestige_bands": {"newbie": [0, 500]},
        "projection": {"guest_template_keys": [], "gear_template_keys": []},
    }
    out = StringIO()

    call_command(
        "generate_virtual_players",
        "--region",
        "north",
        "--prestige-band",
        "newbie",
        "--count",
        "3",
        "--dry-run",
        stdout=out,
        verbosity=1,
    )

    assert BotProfile.objects.count() == 0
    assert "dry-run" in out.getvalue()


@pytest.mark.django_db
def test_generate_virtual_players_command_creates_requested_count(settings):
    from gameplay.constants import BuildingKeys
    from gameplay.models import BotProfile, BuildingType

    for key in (
        BuildingKeys.SILVER_VAULT,
        BuildingKeys.GRANARY,
        BuildingKeys.JUXIAN_ZHUANG,
        BuildingKeys.JIADING_FANG,
        BuildingKeys.YOUXIA_BAOTA,
        BuildingKeys.LIANGGONG_CHANG,
    ):
        BuildingType.objects.get_or_create(key=key, defaults={"name": key, "resource_type": "silver", "base_cost": {}})

    settings.VIRTUAL_PLAYER_CONFIG = {
        "prestige_bands": {"newbie": [0, 500]},
        "projection": {"guest_template_keys": [], "gear_template_keys": []},
    }

    call_command(
        "generate_virtual_players",
        "--region",
        "north",
        "--prestige-band",
        "newbie",
        "--count",
        "2",
        verbosity=0,
    )

    assert BotProfile.objects.filter(manor__region="north", prestige_band="newbie").count() == 2
    assert BotProfile.objects.filter(manor__region="north", target_prestige_band="newbie").count() == 2
    assert BotProfile.objects.filter(manor__region="north", current_prestige_band="newbie").count() == 2
