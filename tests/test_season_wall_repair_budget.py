"""维修额度按据点休整、赛季时区、实际维修与迁移生效时刻结算。"""

from copy import deepcopy
from datetime import datetime, time, timedelta
from importlib import import_module
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from django.apps import apps
from django.db import connection, transaction
from django.template.loader import render_to_string
from django.utils import timezone

from gameplay.services.season.recovery import (
    next_repair_plan,
    recovery_status,
    repair_material_reserve,
    settle_auto_repairs_locked,
)
from gameplay.services.season.warfare import capture_locked
from tests.test_season_occupation_recovery import occupation_world as _occupation_world  # noqa: F401
from tests.test_season_resource_integration import resource_campaign as _resource_campaign  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(request):
    season, site, _, clock = request.getfixturevalue("_occupation_world")
    zone = ZoneInfo(season.rules["campaign"]["expeditions"]["timezone"])
    base = datetime.combine(timezone.localdate(clock[0], timezone=zone), time(hour=12), tzinfo=zone)
    site.owner = "fiction"
    site.durability = 1000000
    site.defenses = {"auto_repair_at": base.isoformat(), "arrow_tower": {"hp": 100000, "max_hp": 100000}}
    site.protected_until = base - timedelta(seconds=1)
    site.supplies = {"wood": 10000, "iron": 10000}
    site.save()
    return season, site, base


def settle(season, site, at):
    with transaction.atomic():
        settle_auto_repairs_locked(season, at)
    site.refresh_from_db()


def test_rest_preserves_full_repair_then_daily_budget_limits_wall(world):
    season, site, base = world
    site.protected_until = base + timedelta(hours=1)
    site.save(update_fields=["protected_until"])
    settle(season, site, base + timedelta(hours=1, minutes=30))
    assert site.durability == 1250000
    assert site.defenses["wall_repair_daily"] == {"date": base.date().isoformat(), "used": 50000}
    assert site.supplies == {"wood": 9700, "iron": 9850}


def test_delayed_scan_resets_budget_on_local_midnight_and_does_not_reset_on_damage(world):
    season, site, base = world
    base = base.replace(hour=23)
    site.defenses["auto_repair_at"] = base.isoformat()
    site.protected_until = base - timedelta(hours=1)
    site.save(update_fields=["defenses", "protected_until"])
    settle(season, site, base + timedelta(hours=1, minutes=30))
    assert site.durability == 1100000  # 23:30 及 00:00 各修五万，00:30 已用完。
    assert site.defenses["wall_repair_daily"] == {"date": (base + timedelta(hours=1)).date().isoformat(), "used": 50000}
    site.durability -= 1
    site.save(update_fields=["durability"])
    settle(season, site, base + timedelta(hours=2))
    assert site.durability == 1099999


def test_budget_midnight_follows_configured_timezone(world):
    season, site, base = world
    season.rules["campaign"]["expeditions"]["timezone"] = "UTC"
    season.save(update_fields=["rules"])
    base = base.astimezone(ZoneInfo("UTC")).replace(hour=23)
    site.defenses["auto_repair_at"] = base.isoformat()
    site.protected_until = base - timedelta(hours=1)
    site.save(update_fields=["defenses", "protected_until"])
    settle(season, site, base + timedelta(hours=1))
    assert site.durability == 1100000
    assert site.defenses["wall_repair_daily"] == {"date": (base + timedelta(hours=1)).date().isoformat(), "used": 50000}


def test_tower_uses_remaining_work_even_when_wall_budget_exhausted(world):
    season, site, base = world
    site.defenses["arrow_tower"]["hp"] = 0
    site.save(update_fields=["defenses"])
    settle(season, site, base + timedelta(minutes=30))
    assert site.durability == 1050000 and site.defenses["arrow_tower"]["hp"] == 50000
    status = recovery_status(site, base + timedelta(minutes=30))
    assert status["budget_exhausted"] and not status["paused"]
    assert status["estimated_wall"] == 0 and status["estimated_tower"] == 50000
    settle(season, site, base + timedelta(hours=1))
    assert site.durability == 1050000 and site.defenses["arrow_tower"]["hp"] == 100000
    status = recovery_status(site, base + timedelta(hours=1))
    assert status["budget_exhausted"] and status["paused"] and not status["short"]
    assert status["estimated_cost"] == {"wood": 0, "iron": 0}
    assert status["budget_resets_at"].date() == (base + timedelta(days=1)).date()
    site.supplies = {}
    site.save(update_fields=["supplies"])
    assert repair_material_reserve(season, "fiction", now=base + timedelta(hours=1)) == {"wood": 0, "iron": 0}


def test_building_details_distinguish_budget_tower_and_material_shortage(world):
    season, site, base = world
    settle(season, site, base + timedelta(minutes=30))

    def details(at):
        return render_to_string(
            "gameplay/season/node_details.html",
            {
                "node": {
                    "site": {"recovery": recovery_status(site, at), "tower": site.defenses["arrow_tower"]},
                    "occupation_class": "fiction",
                    "site_supplies": site.supplies,
                },
                "member": SimpleNamespace(faction="fiction"),
                "hitpoints": {"enabled": True, "gate_normal_rounds": 8},
                "occupation": season.rules["campaign"]["occupation"],
            },
        )

    html = details(base + timedelta(minutes=30))
    assert "城门今日维修额度已用完" in html and "预计修复" not in html
    site.defenses["arrow_tower"]["hp"] = 50000
    site.save(update_fields=["defenses"])
    html = details(base + timedelta(minutes=30))
    assert "预计修复箭塔 50000" in html and "城门今日维修额度已用完" in html
    site.supplies = {}
    site.save(update_fields=["supplies"])
    html = details(base + timedelta(minutes=30))
    assert "城门今日维修额度已用完" in html and "箭塔修复还需木铁" in html


def test_budget_counts_only_actual_repair_when_materials_are_short(world):
    season, site, base = world
    site.supplies = {"wood": 30, "iron": 60}
    site.save(update_fields=["supplies"])
    settle(season, site, base + timedelta(minutes=30))
    assert site.durability == 1025000
    assert site.defenses["wall_repair_daily"]["used"] == 25000
    status = recovery_status(site, base + timedelta(minutes=30))
    assert status["paused"] and not status["budget_exhausted"]
    site.supplies = {"wood": 100, "iron": 100}
    site.save(update_fields=["supplies"])
    assert next_repair_plan(site, affordable=True, now=base + timedelta(minutes=30))[0] == 25000
    settle(season, site, base + timedelta(hours=1))
    assert site.durability == 1050000 and site.defenses["wall_repair_daily"]["used"] == 50000


def test_legacy_site_with_partial_profile_uses_configured_maximum(world):
    season, site, base = world
    settle(season, site, base + timedelta(minutes=30))
    site.npc_profile = {"level": 10}
    site.save(update_fields=["npc_profile"])
    status = recovery_status(site, base + timedelta(minutes=30))
    assert status["budget_exhausted"] and status["paused"] and not status["complete"]


def test_old_ticks_and_old_snapshots_keep_original_repair_rules(world):
    season, site, base = world
    config = season.rules["campaign"]["occupation"]
    config["wall_repair_daily_effective_at"] = (base + timedelta(hours=1)).isoformat()
    season.save(update_fields=["rules"])
    settle(season, site, base + timedelta(hours=1, minutes=30))
    assert site.durability == 1250000
    assert site.defenses["wall_repair_daily"]["used"] == 50000
    config.pop("wall_repair_daily_limit")
    config.pop("wall_repair_daily_effective_at")
    season.save(update_fields=["rules"])
    settle(season, site, base + timedelta(hours=2))
    assert site.durability == 1350000


def test_capture_keeps_same_day_budget_but_rest_repairs_do_not_consume_it(world):
    season, site, base = world
    settle(season, site, base + timedelta(minutes=30))
    book = deepcopy(site.defenses["wall_repair_daily"])
    season.rules["campaign"]["sites"][site.key]["protection_seconds"] = 3600
    season.save(update_fields=["rules"])
    site.owner, site.durability = "history", 0
    site.save(update_fields=["owner", "durability"])
    with transaction.atomic():
        assert capture_locked(site, "fiction", base + timedelta(minutes=30))
        site.save()
    assert site.defenses["wall_repair_daily"] == book
    settle(season, site, base + timedelta(hours=1, minutes=30))
    assert site.durability == 200001
    assert site.defenses["wall_repair_daily"] == book
    settle(season, site, base + timedelta(hours=2))
    assert site.durability == 200001


def test_migration_preserves_progress_and_custom_or_disabled_rules(world, monkeypatch):
    season, site, base = world
    from gameplay.models import Season

    config = season.rules["campaign"]["occupation"]
    config.pop("wall_repair_daily_limit")
    season.save(update_fields=["rules"])
    disabled_rules = deepcopy(season.rules)
    disabled_rules["campaign"]["occupation"]["automatic_repair"] = False
    disabled = Season.objects.create(
        name="停用自动维修", starts_at=season.starts_at, ends_at=season.ends_at, rules=disabled_rules
    )
    archived = Season.objects.create(
        name="历史赛季", starts_at=season.starts_at, ends_at=season.ends_at, rules=deepcopy(season.rules), closed=True
    )
    before = (site.durability, deepcopy(site.defenses), deepcopy(site.supplies))
    migration = import_module("gameplay.migrations.0267_season_wall_repair_budget")
    monkeypatch.setattr(migration.timezone, "now", lambda: base)
    with transaction.atomic():
        migration.enable_wall_repair_budget(apps, SimpleNamespace(connection=connection))
        migration.enable_wall_repair_budget(apps, SimpleNamespace(connection=connection))
    season.refresh_from_db()
    site.refresh_from_db()
    assert season.rules["campaign"]["occupation"]["wall_repair_daily_limit"] == 50000
    assert season.rules["campaign"]["occupation"]["wall_repair_daily_effective_at"] == base.isoformat()
    assert (site.durability, site.defenses, site.supplies) == before
    disabled.refresh_from_db()
    archived.refresh_from_db()
    assert "wall_repair_daily_limit" not in disabled.rules["campaign"]["occupation"]
    assert "wall_repair_daily_limit" not in archived.rules["campaign"]["occupation"]
    custom = deepcopy(season.rules)
    custom["campaign"]["occupation"]["wall_repair_daily_limit"] = 70000
    season.rules = custom
    season.save(update_fields=["rules"])
    with transaction.atomic():
        migration.enable_wall_repair_budget(apps, SimpleNamespace(connection=connection))
    assert Season.objects.get(pk=season.pk).rules["campaign"]["occupation"]["wall_repair_daily_limit"] == 70000
