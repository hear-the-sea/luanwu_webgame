"""真实任务入口覆盖箭塔、城防归零占领及保留进度迁移。"""

from copy import deepcopy
from importlib import import_module
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.apps import apps
from django.db import connection, transaction

from gameplay.models import SeasonExpedition, SeasonRally
from gameplay.services.season.actions import perform_action
from gameplay.services.season.expeditions import process_expedition
from gameplay.services.season.fortifications import DEFAULT_FORTIFICATIONS, tower_state
from gameplay.services.season.population import npc_profile
from gameplay.services.season.warfare import capture_locked
from tests.test_season_resource_integration import resource_campaign as _resource_campaign  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def fort_world(request):
    season, player, clock = request.getfixturevalue("_resource_campaign")
    season.rules["campaign"]["fortifications"] = deepcopy(DEFAULT_FORTIFICATIONS)
    season.save(update_fields=["rules"])
    site = season.sites.get(key="mountain:gate")
    site.owner = "history"
    site.guards = 0
    site.npc_profile = npc_profile(season, site.key)
    site.durability = 20
    site.defenses = {"arrow_tower": {"hp": 0, "max_hp": 300}}
    site.save(update_fields=["owner", "guards", "npc_profile", "durability", "defenses"])
    return season, player, clock, site


def launch(world, *, mode="siege", troops=100):
    season, player, clock, _ = world
    member, guest = player()
    state = season.faction_states.get(faction=member.faction)
    state.troops["dao_sheng"] = 1000
    state.save(update_fields=["troops"])
    result = perform_action(
        member.manor,
        season.pk,
        "rally_create",
        str(uuid4()),
        {
            "mode": mode,
            "node_key": "mountain:gate",
            "guest_ids": [guest.pk],
            "troops": {"dao_sheng": troops},
            "rally_target_members": 1,
        },
    )
    run = SeasonExpedition.objects.get(pk=result["run_id"])
    return member, run, SeasonRally.objects.get(pk=result["rally_id"])


def test_last_wall_damage_captures_same_wave_without_control_phase(fort_world):
    season, _, clock, site = fort_world
    member, run, rally = launch(fort_world)
    assert process_expedition(run.pk, run.due_at)
    site.refresh_from_db()
    run.refresh_from_db()
    rally.refresh_from_db()
    assert site.owner == "fiction" and site.capture_cycle == 1
    assert site.control == 0 and site.durability == site.npc_profile["durability"]
    assert site.durability == season.rules["campaign"]["sites"][site.key]["durability"] * 2
    assert tower_state(site) == {"hp": 300, "max_hp": 300}
    assert rally.status == "resolved" and run.status == "returning"
    # capture重置城防不能抹去最后一次拆墙贡献。
    progress = member.actions.filter(request_hash="site-progress", result__run_id=run.pk)
    assert any(row.result.get("requested_merit", 0) > 0 for row in progress)
    assert not process_expedition(run.pk, run.due_at.replace(year=run.due_at.year - 1))
    assert site.capture_cycle == 1


def test_tower_only_battle_then_breach_uses_no_fictitious_defenders(fort_world):
    _, _, _, site = fort_world
    site.defenses["arrow_tower"]["hp"] = 300
    site.save(update_fields=["defenses"])
    _, run, rally = launch(fort_world)
    assert process_expedition(run.pk, run.due_at)
    run.refresh_from_db()
    site.refresh_from_db()
    assert run.report.winner == "attacker"
    assert run.report.defender_team == []
    assert run.report.defender_city_defenses[0]["destroyed"]
    assert tower_state(site)["hp"] == 0 and site.durability == 20
    assert run.result["fatigue_stacks"] == 1
    assert run.report.losses["season_effects"]["attacker"][0]["fatigue_stacks"] == 0
    assert process_expedition(run.pk, run.due_at)
    run.refresh_from_db()
    site.refresh_from_db()
    assert site.owner == "fiction" and run.status == "returning"
    assert run.result["fatigue_stacks"] == 1  # 单独拆墙不触发新疲敝。


def test_breach_capture_requires_actual_wall_and_tower_clear(fort_world):
    _, _, clock, site = fort_world
    with transaction.atomic():
        assert not capture_locked(site, "fiction", clock[0])
        site.durability = 0
        site.guards = 1
        assert not capture_locked(site, "fiction", clock[0])
        site.guards = 0
        site.defenses["arrow_tower"]["hp"] = 1
        assert not capture_locked(site, "fiction", clock[0])
        site.defenses["arrow_tower"]["hp"] = 0
        assert capture_locked(site, "fiction", clock[0])


def test_rollout_preserves_damage_ratio_owner_resources_and_is_idempotent(fort_world):
    season, _, _, site = fort_world
    season.rules["campaign"].pop("fortifications")
    season.save(update_fields=["rules"])
    site.npc_profile["durability"] = 600
    site.durability = 450
    site.supplies = {"grain": 123, "wood": 99, "iron": 88}
    site.save(update_fields=["npc_profile", "durability", "supplies"])
    resources = list(season.faction_states.values("id", "resources", "troops"))
    migration = import_module("gameplay.migrations.0255_season_fortifications")
    migration.enable_fortifications(apps, SimpleNamespace(connection=connection))
    migration.enable_fortifications(apps, SimpleNamespace(connection=connection))
    site.refresh_from_db()
    season.refresh_from_db()
    assert site.npc_profile["durability"] == 1200 and site.durability == 900
    assert site.owner == "history" and site.capture_cycle == 0
    assert site.supplies == {"grain": 123, "wood": 99, "iron": 88}
    assert list(season.faction_states.values("id", "resources", "troops")) == resources
    assert season.rules["campaign"]["fortifications"]["capture_on_breach"]


def test_existing_zero_wall_does_not_award_removed_control_progress(fort_world):
    _, _, _, site = fort_world
    site.durability = 0
    site.save(update_fields=["durability"])
    member, run, _ = launch(fort_world)
    assert process_expedition(run.pk, run.due_at)
    site.refresh_from_db()
    assert site.owner == "fiction"
    rows = member.actions.filter(request_hash="site-progress", result__run_id=run.pk)
    assert rows.exists() and all(row.result.get("requested_merit", 0) == 0 for row in rows)
