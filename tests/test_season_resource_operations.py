"""实际行军、战粮、军械和维修费用，不混用货物与预留。"""

from datetime import timedelta
from math import ceil

import pytest
from django.db import transaction

from gameplay.services.season.actions import perform_action
from gameplay.services.season.operation_costs import charge_demolition_locked
from gameplay.services.season.supply import settle_supplies_locked
from tests.test_season_resource_integration import advance_resource_run, finish_resource_run, issue
from tests.test_season_resource_integration import resource_campaign as _resource_campaign  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def resource_campaign(request):
    return request.getfixturevalue("_resource_campaign")


def arm(season, member):
    state = season.faction_states.get(faction=member.faction)
    state.troops["dao_sheng"] = 1000
    state.troops["bloodthirsty_cavalry"] = 100
    state.save(update_fields=["troops"])
    return state


def test_return_march_prepayment_refunds_unused_after_early_recall(resource_campaign):
    from uuid import uuid4

    season, player, clock = resource_campaign
    member, guest = player()
    state = arm(season, member)
    before = state.resources["grain"]
    run = issue(member, guest, clock, troops={"dao_sheng": 100})
    clock[0] += timedelta(seconds=10)
    perform_action(member.manor, season.pk, "recall", str(uuid4()), {"run_id": run.pk})
    run.refresh_from_db()
    finish_resource_run(run, clock)
    state.refresh_from_db()
    assert state.resources["grain"] == before - 2 * ceil(100 * 4 * 10 / 60000)
    assert run.result["operation_costs"]["march_balance"] == 0
    assert run.result["operation_costs"]["march_spent"] == 2


def test_courier_fee_is_separate_from_delivered_cargo_and_charged_once(resource_campaign):
    season, player, clock = resource_campaign
    member, guest = player()
    state = arm(season, member)
    site = season.sites.get(key="mountain:gate")
    site.owner = member.faction
    site.durability -= 100
    site.save(update_fields=["owner", "durability"])
    before = state.resources.copy()
    run = issue(member, guest, clock, mode="transport", node="river:docks", target_site_key=site.key)
    assert run.cargo == {"grain": 0, "wood": 120, "iron": 60}
    finish_resource_run(run, clock)
    state.refresh_from_db()
    assert state.resources == {"grain": before["grain"] - 5, "wood": before["wood"] - 120, "iron": before["iron"] - 60}
    assert run.result["operation_costs"]["courier_spent"] == 5


def test_demolition_group_pays_actual_progress_once_instead_of_per_player(resource_campaign):
    season, player, clock = resource_campaign
    a, ag = player()
    b, bg = player()
    state = arm(season, a)
    first = issue(a, ag, clock)
    second = issue(b, bg, clock)
    with transaction.atomic():
        charge_demolition_locked(
            [first, second], {first.pk: {"dao_sheng": 100}, second.pk: {"dao_sheng": 100}}, 25, clock[0]
        )
    state.refresh_from_db()
    assert state.resources["wood"] == 9980
    assert state.resources["iron"] == 9987
    assert sum(run.result["operation_costs"]["expenses"][-1]["resources"]["wood"] for run in (first, second)) == 20


def test_repair_only_pays_actual_missing_wall_and_returns_unused_materials_locally(resource_campaign):
    season, player, clock = resource_campaign
    member, guest = player()
    site = season.sites.get(key="mountain:gate")
    maximum = site.durability
    site.owner = member.faction
    site.durability -= 100
    site.supplies = {"grain": 0, "wood": 120, "iron": 60}
    site.save(update_fields=["owner", "durability", "supplies"])
    run = issue(member, guest, clock, mode="repair", node=site.key)
    site.durability += 50
    site.save(update_fields=["durability"])
    advance_resource_run(run, clock)
    site.refresh_from_db()
    assert site.durability == maximum
    assert site.supplies["wood"] == 60 and site.supplies["iron"] == 30
    line = next(row for row in run.result["operation_costs"]["expenses"] if row["kind"] == "repair")
    assert line["actual_wall"] == 50 and line["resources"] == {"wood": 60, "iron": 30}


def test_garrison_fractional_food_is_actual_time_with_cavalry_weight(resource_campaign):
    season, player, clock = resource_campaign
    member, guest = player()
    arm(season, member)
    site = season.sites.get(key="mountain:gate")
    site.owner = member.faction
    site.supplies = {"grain": 100}
    site.save(update_fields=["owner", "supplies"])
    run = issue(
        member, guest, clock, mode="garrison", node=site.key, troops={"dao_sheng": 100, "bloodthirsty_cavalry": 10}
    )
    advance_resource_run(run, clock)
    start = clock[0]
    for minute in (5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55):
        with transaction.atomic():
            settle_supplies_locked(season, start + timedelta(minutes=minute))
    advance_resource_run(run, clock)
    site.refresh_from_db()
    assert site.supplies["grain"] == 89  # 130等效人×8粮/百人/小时，归队前尾数仅取整一次。
