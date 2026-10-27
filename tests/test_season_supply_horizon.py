"""补给需求和公共粮草保供只计算仍会发生的驻守消耗。"""

from datetime import timedelta

import pytest

from gameplay.models import SeasonExpedition
from gameplay.services.season.logistics import garrison_supply_seconds, site_demand, site_requirements
from gameplay.services.season.resource_production import _protected_grain
from gameplay.services.season.resource_rules import resource_rules
from tests.test_season_resource_integration import resource_campaign as _resource_campaign  # noqa: F401

pytestmark = pytest.mark.django_db


def station(world, *, seconds=3600, status="stationed", carried=0, local=0, cavalry=False):
    season, player, clock = world
    member, _ = player()
    site = season.sites.get(key="mountain:gate")
    site.owner = member.faction
    site.supplies = {"grain": local}
    site.save(update_fields=["owner", "supplies"])
    now = clock[0]
    run = SeasonExpedition.objects.create(
        member=member,
        node_key=site.key,
        kind="garrison",
        status=status,
        troops={"bloodthirsty_cavalry" if cavalry else "dao_sheng": 100},
        launched_at=now if status == "outbound" else now - timedelta(minutes=15),
        travel_seconds=900,
        due_at=now + timedelta(seconds=900 if status == "outbound" else seconds),
        stationed_until=None if status == "outbound" else now + timedelta(seconds=seconds),
        result={
            "station_seconds": seconds,
            "operation_costs": {
                "rules": resource_rules(season)["operations"],
                "carried_food_balance": carried,
            },
        },
    )
    return season, member, site, run, now


@pytest.mark.parametrize(
    "seconds,expected", [(3600, 8), (7200, 16), (14400, 32), (28800, 48), (1800, 4), (1, 1), (0, 0), (-60, 0)]
)
def test_stationed_horizon_tracks_remaining_duty_instead_of_fixed_six_hours(resource_campaign, seconds, expected):
    season, member, site, _, now = station(resource_campaign, seconds=seconds)
    assert site_requirements(season, site, now)["grain"] == expected
    assert _protected_grain(season, member.faction, resource_rules(season)) == expected


@pytest.mark.parametrize("duration,carried,expected", [(3600, 8, 0), (7200, 8, 8), (28800, 8, 40), (28800, 64, 0)])
def test_outbound_budget_uses_frozen_duration_and_deducts_carried_food_once(
    resource_campaign, duration, carried, expected
):
    season, member, site, _, now = station(resource_campaign, seconds=duration, status="outbound", carried=carried)
    assert site_demand(season, site, now)["grain"] == expected
    assert _protected_grain(season, member.faction, resource_rules(season)) == expected


def test_local_food_and_inflight_delivery_reduce_the_same_short_duty_budget_once(resource_campaign):
    season, member, site, _, now = station(resource_campaign, seconds=3600, local=3)
    SeasonExpedition.objects.create(
        member=member,
        node_key="river:warehouse",
        kind="transport",
        status="transporting",
        cargo={"grain": 2},
        launched_at=now,
        due_at=now + timedelta(minutes=10),
        result={"activity": {"target_site_key": site.key}},
    )
    assert site_requirements(season, site, now)["grain"] == 8
    assert site_demand(season, site, now)["grain"] == 3
    assert _protected_grain(season, member.faction, resource_rules(season)) == 3


def test_outbound_budget_ends_at_season_cutoff_not_eight_hours_later(resource_campaign):
    season, member, site, run, now = station(resource_campaign, seconds=28800, status="outbound")
    season.ends_at = now + timedelta(minutes=30)
    season.save(update_fields=["ends_at"])
    assert garrison_supply_seconds(season, run, now, 21600) == 900
    assert site_demand(season, site, now)["grain"] == 2
    assert _protected_grain(season, member.faction, resource_rules(season)) == 2


def test_overdue_outbound_guard_still_has_real_post_arrival_budget(resource_campaign):
    season, member, site, run, now = station(resource_campaign, seconds=3600, status="outbound")
    run.launched_at = now - timedelta(hours=1)
    run.due_at = now - timedelta(minutes=45)
    run.save(update_fields=["launched_at", "due_at"])
    assert site_demand(season, site, now)["grain"] == 8
    assert _protected_grain(season, member.faction, resource_rules(season)) == 8


def test_frozen_cavalry_cost_and_remaining_time_are_shared_by_both_budgets(resource_campaign):
    season, member, site, run, now = station(resource_campaign, seconds=1800, cavalry=True)
    season.rules["campaign"]["resource_economy"]["operations"]["cavalry_garrison_cost_multiplier"] = 9
    season.save(update_fields=["rules"])
    assert site_demand(season, site, now)["grain"] == 12
    assert _protected_grain(season, member.faction, resource_rules(season)) == 12
    run.troops = {}
    run.save(update_fields=["troops"])
    assert site_demand(season, site, now)["grain"] == 0
    assert _protected_grain(season, member.faction, resource_rules(season)) == 0
