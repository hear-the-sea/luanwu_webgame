"""断粮时间约束驻守贡献，不受结算扫描或后到补给影响。"""

from datetime import timedelta

import pytest

from gameplay.services.season.expeditions import _start_return
from gameplay.services.season.operation_costs import settle_garrison_cost_locked
from tests.test_season_resource_integration import advance_resource_run, issue
from tests.test_season_resource_integration import resource_campaign as _resource_campaign  # noqa: F401
from tests.test_season_supply_horizon import station

pytestmark = pytest.mark.django_db


@pytest.fixture
def resource_campaign(request):
    return request.getfixturevalue("_resource_campaign")


@pytest.mark.parametrize("cavalry,food,seconds", [(False, 8, 3600), (True, 24, 3600), (False, 0, 0)])
def test_delayed_return_only_rewards_time_supported_by_actual_food(resource_campaign, cavalry, food, seconds):
    _, _, site, run, now = station(resource_campaign, seconds=28800, local=food, cavalry=cavalry)
    run.result = {
        **run.result,
        "stationed_at": now.isoformat(),
        "garrison_merit_per_hour": 10,
        "operation_costs": {**run.result["operation_costs"], "march_balance": 0, "march_spent": 0},
    }
    run.save(update_fields=["result"])
    _start_return(run, now + timedelta(hours=8), "驻守结束")
    run.refresh_from_db()
    site.refresh_from_db()
    assert run.status == "returning"
    assert run.result["garrison_service"]["seconds"] == seconds
    assert run.result["garrison_service"]["merit"] == seconds * 10 // 3600
    assert run.result["supply_exhausted_at"] == (now + timedelta(seconds=seconds)).isoformat()
    assert site.supplies["grain"] == 0


def test_later_food_cannot_reopen_exhausted_garrison_or_charge_it_again(resource_campaign):
    _, _, site, run, now = station(resource_campaign, seconds=28800, local=8)
    run.result = {**run.result, "stationed_at": now.isoformat()}
    run.save(update_fields=["result"])
    assert not settle_garrison_cost_locked(run, site, now + timedelta(hours=2))
    site.supplies = {"grain": 100}
    site.save(update_fields=["supplies"])
    assert not settle_garrison_cost_locked(run, site, now + timedelta(hours=8), final=True)
    site.refresh_from_db()
    assert site.supplies["grain"] == 100
    assert run.stationed_until == now + timedelta(hours=1)


def test_fractional_consumption_preserves_exact_depletion_time(resource_campaign):
    _, _, site, run, now = station(resource_campaign, seconds=28800, local=8)
    run.result = {**run.result, "stationed_at": now.isoformat()}
    run.save(update_fields=["result"])
    assert settle_garrison_cost_locked(run, site, now + timedelta(minutes=10))
    assert run.result["supply_fraction"] == "1/3"
    assert not settle_garrison_cost_locked(run, site, now + timedelta(hours=2))
    assert run.stationed_until == now + timedelta(hours=1)


def test_zero_troops_do_not_exhaust_food_or_shorten_duty(resource_campaign):
    _, _, site, run, now = station(resource_campaign, seconds=28800, local=0)
    run.troops = {}
    run.save(update_fields=["troops"])
    assert settle_garrison_cost_locked(run, site, now + timedelta(hours=8), final=True)
    assert "supply_exhausted_at" not in run.result


@pytest.mark.parametrize("delayed", [False, True])
def test_real_transport_supports_only_garrison_that_has_not_run_out(resource_campaign, delayed):
    season, player, clock = resource_campaign
    season.rules["campaign"]["simplification"]["enabled"] = True
    season.save(update_fields=["rules"])
    member, guest = player()
    carrier, courier = player()
    state = season.faction_states.get(faction=member.faction)
    state.troops["dao_sheng"] = 100
    state.save(update_fields=["troops"])
    site = season.sites.get(key="mountain:gate")
    site.owner = member.faction
    site.save(update_fields=["owner"])
    guard = issue(member, guest, clock, mode="garrison", node=site.key, troops={"dao_sheng": 100}, station_hours=4)
    advance_resource_run(guard, clock)
    arrived = clock[0]
    shipment = issue(carrier, courier, clock, mode="transport", node="river:warehouse", target_site_key=site.key)
    assert shipment.cargo["grain"] == 24
    advance_resource_run(shipment, clock)
    if delayed:
        from gameplay.services.season.expeditions import process_expedition

        clock[0] = arrived + timedelta(hours=2)
        assert process_expedition(shipment.pk, clock[0])
        shipment.refresh_from_db()
    else:
        advance_resource_run(shipment, clock)
        advance_resource_run(guard, clock)
    guard.refresh_from_db()
    assert guard.status == "returning"
    assert guard.result["garrison_service"]["seconds"] == (3600 if delayed else 14400)
    assert guard.result["garrison_service"]["merit"] == (10 if delayed else 40)
    assert shipment.result["delivered_resources"].get("grain", 0) == (0 if delayed else 24)
