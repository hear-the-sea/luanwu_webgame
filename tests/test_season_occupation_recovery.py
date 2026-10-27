"""占领后的1血、自动维修、休整停分与公共资源优先级。"""

from copy import deepcopy
from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection, transaction

from gameplay.services.season.campaign_rules import load_campaign_rules
from gameplay.services.season.population import npc_profile
from gameplay.services.season.recovery import recovery_status, repair_material_reserve, settle_auto_repairs_locked
from gameplay.services.season.resource_production import produce_resources_locked
from gameplay.services.season.warfare import capture_locked, score_sites_locked
from tests.test_season_resource_integration import resource_campaign as _resource_campaign  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def occupation_world(request):
    season, player, clock = request.getfixturevalue("_resource_campaign")
    current = load_campaign_rules()
    for key in ("occupation", "fortifications", "resource_economy"):
        season.rules["campaign"][key] = deepcopy(current[key])
    for key in season.rules["campaign"]["sites"]:
        season.rules["campaign"]["sites"][key]["protection_seconds"] = current["sites"][key]["protection_seconds"]
    for profile in season.population["factions"].values():
        profile["count"] = profile["economic_count"] = 1
    season.save(update_fields=["rules", "population"])
    site = season.sites.get(key="mountain:gate")
    site.owner, site.guards, site.durability = "history", 0, 0
    site.npc_profile = npc_profile(season, site.key)
    site.defenses = {"arrow_tower": {"hp": 0, "max_hp": 100000}}
    site.supplies = {"grain": 500, "wood": 5000, "iron": 5000}
    site.save()
    return season, site, player, clock


def capture(world, at=None):
    season, site, _, clock = world
    with transaction.atomic():
        assert capture_locked(site, "fiction", at or clock[0])
        site.save()
    site.refresh_from_db()
    return season.faction_states.get(faction="fiction")


def recover(world, at):
    season, site, _, _ = world
    with transaction.atomic():
        settle_auto_repairs_locked(season, at)
    site.refresh_from_db()


def test_capture_starts_at_one_hp_awards_once_and_rest_period_has_no_holding_score(occupation_world):
    season, site, _, clock = occupation_world
    state = capture(occupation_world)
    assert site.durability == site.defenses["arrow_tower"]["hp"] == 1
    assert site.protected_until == clock[0] + timedelta(hours=8)
    assert site.last_score_at == site.protected_until
    assert state.score == 2
    with transaction.atomic():
        for hours, expected in ((7, 2), (8, 2), (9, 3), (10, 4), (10, 4)):
            score_sites_locked(season, clock[0] + timedelta(hours=hours))
            state.refresh_from_db()
            assert state.score == expected


def test_capture_score_cannot_be_farmed_twice_by_same_faction_same_site_day(occupation_world):
    _, site, _, clock = occupation_world
    capture(occupation_world)
    site.owner, site.durability = "history", 0
    site.save(update_fields=["owner", "durability"])
    state = capture(occupation_world, clock[0] + timedelta(seconds=2))
    assert state.score == 2 and site.capture_cycle == 2


def test_auto_repair_first_tick_is_after_thirty_minutes_and_finishes_both_in_eight_hours(occupation_world):
    season, site, _, clock = occupation_world
    capture(occupation_world)
    stock = list(season.faction_states.values("resources", "troops"))
    recover(occupation_world, clock[0] + timedelta(minutes=29, seconds=59))
    assert site.durability == 1
    recover(occupation_world, clock[0] + timedelta(minutes=30))
    assert site.durability == 100001
    assert site.defenses["arrow_tower"]["hp"] == 1
    assert site.supplies == {"grain": 500, "wood": 4880, "iron": 4940}
    recover(occupation_world, clock[0] + timedelta(hours=8))
    assert site.durability == 1500000 and site.defenses["arrow_tower"]["hp"] == 100000
    assert site.supplies["wood"] == 3119 and site.supplies["iron"] == 3999
    before = deepcopy(site.supplies)
    recover(occupation_world, clock[0] + timedelta(hours=8))
    assert site.supplies == before
    assert list(season.faction_states.values("resources", "troops")) == stock


def test_no_resources_no_bank_of_repairs_and_later_delivery_waits_for_next_tick(occupation_world):
    _, site, _, clock = occupation_world
    capture(occupation_world)
    site.supplies = {}
    site.save(update_fields=["supplies"])
    recover(occupation_world, clock[0] + timedelta(hours=4))
    assert site.durability == 1
    site.supplies = {"wood": 5000, "iron": 5000}
    site.save(update_fields=["supplies"])
    recover(occupation_world, clock[0] + timedelta(hours=4, minutes=29))
    assert site.durability == 1
    recover(occupation_world, clock[0] + timedelta(hours=4, minutes=30))
    assert site.durability == 100001


def test_fully_healthy_buildings_do_not_bank_free_repair_after_later_damage(occupation_world):
    _, site, _, clock = occupation_world
    capture(occupation_world)
    site.durability = 1500000
    site.defenses["arrow_tower"]["hp"] = 100000
    site.save(update_fields=["durability", "defenses"])
    recover(occupation_world, clock[0] + timedelta(hours=4))
    site.durability -= 50000
    site.save(update_fields=["durability"])
    recover(occupation_world, clock[0] + timedelta(hours=4))
    assert site.durability == 1450000
    recover(occupation_world, clock[0] + timedelta(hours=4, minutes=30))
    assert site.durability == 1500000


def test_partial_automatic_repairs_only_spend_available_materials(occupation_world):
    _, site, _, clock = occupation_world
    capture(occupation_world)
    site.supplies = {"wood": 60, "iron": 60}
    site.save(update_fields=["supplies"])
    recover(occupation_world, clock[0] + timedelta(minutes=30))
    assert site.durability == 50001
    assert site.supplies == {"wood": 0, "iron": 30}


def test_next_repair_preview_is_read_only_and_uses_actual_available_materials(occupation_world):
    _, site, _, clock = occupation_world
    capture(occupation_world)
    site.supplies = {"wood": 60, "iron": 60}
    site.save(update_fields=["supplies"])
    before = (site.durability, deepcopy(site.supplies), deepcopy(site.defenses))
    preview = recovery_status(site, clock[0])
    assert (preview["estimated_wall"], preview["estimated_tower"]) == (50000, 0)
    assert preview["estimated_cost"] == {"wood": 60, "iron": 30}
    assert preview["short"] and not preview["paused"]
    site.refresh_from_db()
    assert (site.durability, site.supplies, site.defenses) == before
    recover(occupation_world, clock[0] + timedelta(minutes=30))
    assert site.durability == 50001
    assert recovery_status(site, clock[0] + timedelta(minutes=30))["paused"]


def test_friendly_site_shows_next_repair_and_cost(occupation_world, client):
    from django.urls import reverse

    season, site, player, _ = occupation_world
    capture(occupation_world)
    member, _ = player("fiction")
    site.supplies = {"wood": 60, "iron": 60}
    site.save(update_fields=["supplies"])
    client.force_login(member.manor.user)
    html = client.get(reverse("gameplay:season"), {"season_id": season.pk}).content.decode()
    assert "预计修复城门 50000" in html
    assert "消耗木材 60、铁料 30" in html


def test_training_preserves_next_repair_materials_and_does_not_consume_them(occupation_world):
    season, site, _, clock = occupation_world
    capture(occupation_world)
    site.supplies = {}
    site.save(update_fields=["supplies"])
    config = season.rules["campaign"]["resource_economy"]
    config["production"]["daily_fixed"] = config["production"]["daily_per_active"] = {"grain": 0, "wood": 0, "iron": 0}
    season.save(update_fields=["rules"])
    state = season.faction_states.get(faction="fiction")
    state.resources = {"grain": 10000, "wood": 120, "iron": 60}
    state.troops = dict.fromkeys(state.troops, 0)
    state.economy_state["opened"] = True
    state.levels["training"] = 1
    state.produced_at = clock[0]
    state.save()
    assert repair_material_reserve(season, "fiction") == {"wood": 120, "iron": 60}
    with transaction.atomic():
        produce_resources_locked(state, season.rules["campaign"]["economy"], clock[0] + timedelta(hours=1), {})
    state.refresh_from_db()
    assert state.resources == {"grain": 10000, "wood": 120, "iron": 60}
    assert not any(state.troops.values())


def test_migration_preserves_existing_hp_and_past_scores_while_extending_current_rest(occupation_world, monkeypatch):
    season, site, _, clock = occupation_world
    season.rules["campaign"].pop("occupation")
    season.rules["campaign"]["sites"][site.key]["protection_seconds"] = 1800
    season.save(update_fields=["rules"])
    site.durability = 700000
    site.defenses = {"arrow_tower": {"hp": 50000, "max_hp": 100000}}
    site.protected_until = clock[0] + timedelta(minutes=10)
    site.last_score_at = clock[0]
    site.save()
    stocks = list(season.faction_states.values("resources", "troops", "score"))
    migration = import_module("gameplay.migrations.0259_season_occupation_recovery")
    monkeypatch.setattr(migration.timezone, "now", lambda: clock[0])
    migration.enable_recovery(apps, SimpleNamespace(connection=connection))
    migration.enable_recovery(apps, SimpleNamespace(connection=connection))
    site.refresh_from_db()
    assert site.durability == 700000 and site.defenses["arrow_tower"]["hp"] == 50000
    assert site.protected_until == clock[0] - timedelta(minutes=20) + timedelta(hours=8)
    assert site.last_score_at == site.protected_until
    assert list(season.faction_states.values("resources", "troops", "score")) == stocks


def test_capture_at_week_boundary_is_in_current_week(occupation_world):
    season, _, _, _ = occupation_world
    at = season.starts_at + timedelta(weeks=1)
    state = capture(occupation_world, at)
    assert state.economy_state["weekly_campaign"]["weeks"]["2"]["capture_points"] == 2


def test_inflight_transport_covers_next_repair_reserve_without_double_counting(occupation_world):
    from tests.test_season_resource_integration import issue

    season, site, player, clock = occupation_world
    capture(occupation_world)
    site.supplies = {}
    site.save(update_fields=["supplies"])
    member, guest = player()
    run = issue(member, guest, clock, mode="transport", node="river:docks", target_site_key=site.key)
    assert run.cargo["wood"] >= 120 and run.cargo["iron"] >= 60
    assert repair_material_reserve(season, member.faction) == {"wood": 0, "iron": 0}


def test_new_battle_cannot_take_grain_reserved_for_existing_garrison(occupation_world):
    from core.exceptions import GameError
    from gameplay.services.season.operation_costs import _spend_public
    from gameplay.services.season.recovery import resource_reserves
    from tests.test_season_resource_integration import advance_resource_run, issue

    season, site, player, clock = occupation_world
    capture(occupation_world)
    site.supplies["grain"] = 0
    site.save(update_fields=["supplies"])
    member, guest = player()
    state = season.faction_states.get(faction=member.faction)
    state.troops["dao_sheng"] = 1000
    state.save(update_fields=["troops"])
    run = issue(member, guest, clock, mode="garrison", node=site.key, troops={"dao_sheng": 100}, station_hours=1)
    advance_resource_run(run, clock)
    reserve = resource_reserves(season, member.faction)
    assert reserve["grain"] > 0
    state.refresh_from_db()
    state.resources["grain"] = reserve["grain"]
    state.save(update_fields=["resources"])
    with transaction.atomic(), pytest.raises(GameError, match="已有驻军补给"):
        _spend_public(member, {"grain": 1}, clock[0], reason="battle")
    state.refresh_from_db()
    assert state.resources["grain"] == reserve["grain"]
