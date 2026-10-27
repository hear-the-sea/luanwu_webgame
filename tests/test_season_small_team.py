"""少人服务器的离线驻军、只读指引与奖励目标回归。"""

from datetime import timedelta

import pytest
from django.core.exceptions import ImproperlyConfigured

from core.exceptions import GameError
from gameplay.selectors.season import get_season_context
from gameplay.selectors.season_guidance import campaign_guidance, reward_progress
from gameplay.services.season.campaign_rules import load_campaign_rules, validate_campaign_rules
from tests.test_season_resource_integration import advance_resource_run, finish_resource_run, issue
from tests.test_season_simplification import _resource_campaign as _resource_campaign  # noqa: F401
from tests.test_season_simplification import simple_world as _simple_world  # noqa: F401


@pytest.fixture
def simple_world(request):
    return request.getfixturevalue("_simple_world")


@pytest.mark.django_db
@pytest.mark.parametrize("hours", [1, 2, 4])
def test_chosen_garrison_hours_carry_real_food_and_return_on_deadline(simple_world, hours):
    season, player, clock = simple_world
    member, guest = player()
    state = season.faction_states.get(faction=member.faction)
    state.troops["dao_sheng"] = 100
    state.save(update_fields=["troops"])
    site = season.sites.get(key="mountain:gate")
    site.owner = member.faction
    site.save(update_fields=["owner"])
    before = state.resources["grain"]
    run = issue(member, guest, clock, mode="garrison", node=site.key, troops={"dao_sheng": 100}, station_hours=hours)
    assert run.result["station_seconds"] == hours * 3600
    assert run.result["operation_costs"]["carried_food_balance"] == 8
    state.refresh_from_db()
    assert state.resources["grain"] == before - 8 - run.result["operation_costs"]["march_balance"]
    advance_resource_run(run, clock)
    assert run.status == "stationed"
    assert run.stationed_until == clock[0] + timedelta(hours=hours)
    advance_resource_run(run, clock)
    assert run.status == "returning"
    finish_resource_run(run, clock)
    assert run.status == "completed"


@pytest.mark.django_db
@pytest.mark.parametrize("hours", [0, 3, 25, True, "4"])
def test_invalid_garrison_duration_reserves_nothing(simple_world, hours):
    season, player, clock = simple_world
    member, guest = player()
    before = list(season.faction_states.values("resources", "troops"))
    with pytest.raises(GameError, match="驻军时长"):
        issue(member, guest, clock, mode="garrison", node="mountain:gate", station_hours=hours)
    assert not member.expeditions.exists()
    assert list(season.faction_states.values("resources", "troops")) == before


@pytest.mark.django_db
def test_guidance_context_and_site_progress_are_read_only(simple_world):
    season, player, _ = simple_world
    member, _ = player()
    before = list(season.sites.values())
    context = get_season_context(member.manor, season.pk)
    assert context["station_hours_options"] == [1, 2, 4, 8]
    assert context["rally_member_options"] == [1, 2, 3, 4, 5]
    assert context["command_guidance"]["main"]["modal"].startswith("season-node-")
    assert context["reward_progress"]["remaining"] == 100
    gate = next(node for region in context["season_maps"] for node in region["nodes"] if node["key"] == "mountain:gate")
    assert gate["site"]["max_durability"] > 0
    assert gate["site"]["control_required"] > 0
    assert list(season.sites.values()) == before


def test_guidance_skips_closed_or_protected_city_and_prefers_joining_team():
    nodes = [
        {"key": "river:town", "name": "古城", "unlocked": True, "rally_modes": [1], "schedule_closed": True},
        {"key": "forest:camp", "name": "营寨", "unlocked": True, "rally_modes": [1]},
    ]

    def guide(rallies=None):
        return campaign_guidance(nodes, rallies or [], [], [], "fiction", "", True)

    assert guide()["main"]["modal"] == "season-node-forest:camp"
    nodes[0]["schedule_closed"] = False
    nodes[0]["site"] = {"protected_until": "future"}
    assert guide()["main"]["modal"] == "season-node-forest:camp"
    assert guide([{"id": 4, "node_name": "青石关", "can_join": True}])["main"]["modal"] == "season-rally-4"
    assert not campaign_guidance(nodes, [], [], [], "", "", True)
    assert not campaign_guidance(nodes, [], [], [], "fiction", "", False)


def test_reward_progress_keeps_unclaimed_reward_and_uses_configured_threshold():
    rewards = [
        {"name": "卷轴", "threshold": 2400, "claimed": False, "available": False},
        {"name": "兵器", "threshold": 100, "claimed": True, "available": False},
    ]
    progress = reward_progress(rewards, 400)
    assert progress["threshold"] == 2400 and progress["remaining"] == 2000
    assert reward_progress(rewards, 2600)["value"] == 2400
    assert not reward_progress([], 0)


@pytest.mark.parametrize("options", [[], [True], [0], [25], [1, 1], "1,2,4"])
def test_invalid_station_hour_config_is_rejected(options):
    rules = load_campaign_rules()
    rules["expeditions"]["station_hours_options"] = options
    with pytest.raises(ImproperlyConfigured, match="驻军|station_hours"):
        validate_campaign_rules(rules)


def test_guidance_does_not_send_players_into_blocked_transport_route():
    nodes = [{"key": "river:warehouse", "transport_route_blocked": True}]
    result = campaign_guidance(nodes, [], [], [{"demand": {"grain": 100}}], "fiction", "", True)
    assert result["support"]["modal"] == "season-node-river:docks"
