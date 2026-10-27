"""箭塔维修按损坏计费、冻结单价、并发退款与后勤需求。"""

from copy import deepcopy

import pytest

from core.exceptions import GameError
from gameplay.services.season.fortifications import DEFAULT_FORTIFICATIONS, needs_repair, tower_state
from gameplay.services.season.logistics import site_requirements
from gameplay.services.season.operation_costs import fortification_repair_plan
from tests.test_season_resource_integration import advance_resource_run, issue
from tests.test_season_resource_integration import resource_campaign as _resource_campaign  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def repair_world(request):
    season, player, clock = request.getfixturevalue("_resource_campaign")
    season.rules["campaign"]["fortifications"] = deepcopy(DEFAULT_FORTIFICATIONS)
    season.save(update_fields=["rules"])
    site = season.sites.get(key="mountain:gate")
    site.owner = "fiction"
    site.durability = site.npc_profile["durability"]
    site.defenses = {"arrow_tower": {"hp": 200, "max_hp": 300}}
    site.supplies = {"grain": 1234, "wood": 500, "iron": 500}
    site.save(update_fields=["owner", "durability", "defenses", "supplies"])
    member, guest = player()
    return season, member, guest, clock, site


def test_tower_only_damage_can_be_repaired_and_costs_are_frozen(repair_world):
    season, member, guest, clock, site = repair_world
    assert needs_repair(site)
    site.defenses["arrow_tower"]["last_report_id"] = 12
    site.save(update_fields=["defenses"])
    run = issue(member, guest, clock, mode="repair", node=site.key)
    assert run.cargo == {"wood": 80, "iron": 100}
    season.rules["campaign"]["fortifications"]["tower"]["repair_cost_per_100"] = {"wood": 800, "iron": 1000}
    season.save(update_fields=["rules"])
    advance_resource_run(run, clock)
    site.refresh_from_db()
    assert tower_state(site)["hp"] == 300 and not needs_repair(site)
    assert site.defenses["arrow_tower"]["last_report_id"] == 12
    assert site.supplies == {"grain": 1234, "wood": 420, "iron": 400}
    line = next(row for row in run.result["operation_costs"]["expenses"] if row["kind"] == "repair")
    assert line["actual_tower"] == 100 and line["actual_wall"] == 0


def test_ally_repairs_before_arrival_only_actual_missing_is_paid(repair_world):
    _, member, guest, clock, site = repair_world
    run = issue(member, guest, clock, mode="repair", node=site.key)
    site.refresh_from_db()
    site.defenses["arrow_tower"]["hp"] = 290
    site.save(update_fields=["defenses"])
    advance_resource_run(run, clock)
    site.refresh_from_db()
    assert tower_state(site)["hp"] == 300
    assert site.supplies == {"grain": 1234, "wood": 492, "iron": 490}
    assert not run.cargo


def test_full_wall_and_tower_reject_without_reserving_materials(repair_world):
    _, member, guest, clock, site = repair_world
    site.defenses["arrow_tower"]["hp"] = 300
    site.save(update_fields=["defenses"])
    before = deepcopy(site.supplies)
    with pytest.raises(GameError, match="无需维修"):
        issue(member, guest, clock, mode="repair", node=site.key)
    site.refresh_from_db()
    assert site.supplies == before and not member.expeditions.exists()


def test_tower_damage_adds_real_wood_iron_logistics_demand(repair_world):
    season, _, _, clock, site = repair_world
    requirements = site_requirements(season, site, clock[0])
    assert requirements["wood"] == 80 and requirements["iron"] == 100
    site.defenses["arrow_tower"]["hp"] = 300
    requirements = site_requirements(season, site, clock[0])
    assert requirements["wood"] == 0 and requirements["iron"] == 0


def test_repair_work_is_shared_wall_first_and_never_exceeds_reserved_cargo():
    snapshot = {"work_amount": 100, "wall_prices": {"wood": 120, "iron": 60}, "tower_prices": {"wood": 80, "iron": 100}}
    assert fortification_repair_plan(snapshot, 20, 200) == (20, 80, {"wood": 88, "iron": 92})
    wall, tower, cost = fortification_repair_plan(snapshot, 100, 200, {"wood": 80, "iron": 100})
    assert wall + tower <= 100 and cost["wood"] <= 80 and cost["iron"] <= 100
