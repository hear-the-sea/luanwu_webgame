"""建筑血量继承、真实伤害、维修冷却与费用缩放的入口回归。"""

from copy import deepcopy
from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.apps import apps
from django.db import connection

from core.exceptions import GameError
from gameplay.models import SeasonExpedition, SeasonRally
from gameplay.services.season.actions import perform_action
from gameplay.services.season.campaign_rules import load_campaign_rules
from gameplay.services.season.expeditions import process_expedition
from gameplay.services.season.fortifications import tower_state
from gameplay.services.season.population import npc_profile
from tests.test_season_resource_integration import advance_resource_run, issue
from tests.test_season_resource_integration import resource_campaign as _resource_campaign  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def hp_world(request):
    season, player, clock = request.getfixturevalue("_resource_campaign")
    season.rules["campaign"]["fortifications"] = load_campaign_rules()["fortifications"]
    season.save(update_fields=["rules"])
    site = season.sites.get(key="mountain:gate")
    site.guards = 0
    site.owner = "history"
    site.npc_profile = npc_profile(season, site.key)
    site.durability = site.npc_profile["durability"]
    site.defenses = {"arrow_tower": {"hp": 100000, "max_hp": 100000}}
    site.supplies = {"grain": 500, "wood": 1000, "iron": 1000}
    site.save()
    return season, player, clock, site


def attack(world):
    season, player, _, _ = world
    member, guest = player()
    guest.force = guest.intellect = guest.agility = guest.defense_stat = 100
    guest.current_hp = 3000
    guest.save()
    state = season.faction_states.get(faction=member.faction)
    state.troops["dao_sheng"] = 1000
    state.save(update_fields=["troops"])
    result = perform_action(
        member.manor,
        season.pk,
        "rally_create",
        str(uuid4()),
        {
            "mode": "siege",
            "node_key": "mountain:gate",
            "guest_ids": [guest.pk],
            "troops": {"dao_sheng": 10},
            "rally_target_members": 1,
        },
    )
    return member, SeasonExpedition.objects.get(pk=result["run_id"]), SeasonRally.objects.get(pk=result["rally_id"])


def test_building_damage_is_real_and_both_hp_carry_to_next_battle(hp_world):
    # 单独验证跨战斗血量继承，自动维修的时间推进在专项中核对。
    hp_world[0].rules["campaign"]["occupation"]["automatic_repair"] = False
    hp_world[0].save(update_fields=["rules"])
    _, _, clock, site = hp_world
    _, run, rally = attack(hp_world)
    assert process_expedition(run.pk, run.due_at)
    site.refresh_from_db()
    run.refresh_from_db()
    assert 0 < site.durability < 1500000
    rally.refresh_from_db()
    assert run.status == "returning" and run.report.winner == "defender"
    assert rally.status == "resolved"
    assert len([row for row in run.report.rounds if row.get("priority", 0) >= 0]) == 8
    gate_hp, tower_hp = site.durability, tower_state(site)["hp"]
    rows = {row["key"]: row for row in run.report.defender_city_defenses}
    assert rows["wall"]["hp"] == gate_hp
    assert rows["arrow_tower"]["hp"] == tower_hp  # 没有场外额外磨损。
    assert not any(row["kind"] == "demolition" for row in run.result["operation_costs"]["expenses"])
    clock[0] = run.due_at + timedelta(seconds=1)
    _, second, _ = attack(hp_world)
    assert process_expedition(second.pk, second.due_at)
    site.refresh_from_db()
    second.refresh_from_db()
    rows = {row["key"]: row for row in second.report.defender_city_defenses}
    assert rows["wall"]["initial_hp"] == gate_hp
    if tower_hp:
        assert rows["arrow_tower"]["initial_hp"] == tower_hp
    assert site.durability <= gate_hp


def test_gate_death_captures_immediately_even_if_tower_remains(hp_world):
    _, _, _, site = hp_world
    site.durability = 0
    site.save(update_fields=["durability"])
    _, run, rally = attack(hp_world)
    assert process_expedition(run.pk, run.due_at)
    site.refresh_from_db()
    rally.refresh_from_db()
    assert site.owner == "fiction" and rally.status == "resolved"


def test_repair_heals_actual_hp_and_building_cooldown_blocks_other_players(hp_world):
    # 旧手动维修快照继续兼容，自动维修另有专项覆盖。
    hp_world[0].rules["campaign"]["occupation"]["enabled"] = False
    hp_world[0].save(update_fields=["rules"])
    _, player, clock, site = hp_world
    member, guest = player("history")
    site.durability -= 50000
    site.defenses["arrow_tower"]["hp"] = 50000
    site.save()
    first = issue(member, guest, clock, mode="repair", node=site.key)
    assert first.cargo == {"wood": 100, "iron": 80}
    other, other_guest = player("history")
    from gameplay.selectors.season import get_season_context

    context = get_season_context(other.manor, other.season_id)
    node = next(node for region in context["season_maps"] for node in region["nodes"] if node["key"] == site.key)
    assert all(row["key"] != "repair" for row in node["personal_modes"])
    assert node["site"]["repair_ready_at"]
    with pytest.raises(GameError, match="维修冷却"):
        issue(other, other_guest, clock, mode="repair", node=site.key)
    advance_resource_run(first, clock)
    site.refresh_from_db()
    assert site.durability == 1500000 and tower_state(site)["hp"] == 100000
    assert site.supplies == {"grain": 500, "wood": 900, "iron": 920}
    clock[0] += timedelta(minutes=31)
    site.durability -= 10000
    site.save(update_fields=["durability"])
    next_run = issue(other, other_guest, clock, mode="repair", node=site.key)
    assert next_run.cargo == {"wood": 12, "iron": 6}


def test_hp_migration_preserves_damage_ratio_and_progress(hp_world):
    season, _, _, site = hp_world
    season.rules["campaign"]["fortifications"].pop("hitpoints")
    season.save(update_fields=["rules"])
    site.npc_profile["durability"] = 1200
    site.durability = 600
    site.defenses = {"arrow_tower": {"hp": 150, "max_hp": 300}}
    site.save()
    before = deepcopy(site.supplies)
    migration = import_module("gameplay.migrations.0256_season_structure_hitpoints")
    migration.enable_hitpoints(apps, SimpleNamespace(connection=connection))
    migration.enable_hitpoints(apps, SimpleNamespace(connection=connection))
    site.refresh_from_db()
    assert site.durability == 750000 and site.npc_profile["durability"] == 1500000
    assert site.defenses["arrow_tower"]["hp"] == 50000
    assert site.owner == "history" and site.supplies == before
