"""地图提示由真实公开状态选择，优先显示可操作事项。"""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from gameplay.selectors.season_map_info import decorate_map_nodes
from tests.test_season_resource_integration import resource_campaign as _resource_campaign  # noqa: F401


@pytest.fixture
def resource_campaign(request):
    return request.getfixturevalue("_resource_campaign")


def test_map_prefers_joinable_rally_then_real_supply_and_does_not_show_enemy_names():
    node = {
        "key": "mountain:gate",
        "rallies": [{"can_join": True, "member_count": 1, "target_members": 3}],
        "garrison": {"member_count": 2, "players": [{"name": "敌方秘密门客"}]},
        "occupation_class": "history",
    }
    maps = [{"nodes": [node]}]
    decorate_map_nodes(maps, {}, [{"key": node["key"], "demand": {"grain": 30}}], [], "fiction")
    assert node["map_hint"] == "集结 1/3 · 可加入"
    assert "秘密" not in node["map_hint"]
    node["rallies"] = []
    decorate_map_nodes(maps, {}, [{"key": node["key"], "demand": {"grain": 30}}], [], "fiction")
    assert node["map_hint"] == "缺粮待援"


def test_map_shows_own_run_or_closed_city_instead_of_false_attack_hint():
    node = {"key": "river:town", "schedule_closed": True}
    maps = [{"nodes": [node]}]
    guide = {"main": {"modal": "season-node-river:town"}}
    decorate_map_nodes(maps, guide, [], [], "fiction")
    assert node["map_hint"] == "周末开战"
    node["schedule_closed"] = False
    decorate_map_nodes(maps, guide, [], [SimpleNamespace(node_key="river:town")], "fiction")
    assert node["map_hint"] == "我的队伍 1 支"
    decorate_map_nodes(maps, guide, [], [], "fiction")
    assert node["map_hint"] == "建议主攻"


def test_map_default_roles_use_same_reusable_sign_without_extra_actions():
    maps = [{"nodes": [{"key": "mountain:inn"}, {"key": "river:warehouse"}]}]
    decorate_map_nodes(maps, {}, [], [], "")
    assert maps[0]["nodes"][0]["map_hint"] == "粮草收集"
    assert maps[0]["nodes"][1]["map_hint"] == "运输补给与兵库"


def test_existing_rallies_keep_a_pane_when_new_dispatch_is_unavailable():
    for overrides in ({}, {"schedule_closed": True}, {"site": {"protected_until": "future"}}):
        node = {
            "key": "mountain:gate",
            "rally_modes": [],
            "occupation_class": "fiction",
            "site": {},
            "rallies": [{"can_join": False}],
            **overrides,
        }
        maps = [{"nodes": [node]}]
        decorate_map_nodes(maps, {}, [], [], "fiction")
        assert "rally" in {pane["key"] for pane in node["popup_panes"]}
        assert maps[0]["joinable_rally_count"] == 0


def test_market_uses_war_pane_for_existing_rally_without_exposing_enemy_trade():
    node = {
        "key": "river:market",
        "site": {"guards": 1},
        "occupation_class": "history",
        "rallies": [{"can_join": False}],
    }
    decorate_map_nodes([{"nodes": [node]}], {}, [], [], "fiction")
    assert [pane["key"] for pane in node["popup_panes"]] == ["war"]


def test_cross_region_badges_count_only_joinable_rallies():
    maps = [
        {
            "key": "mountain",
            "nodes": [
                {
                    "key": "mountain:gate",
                    "rallies": [
                        {"can_join": True, "member_count": 1, "target_members": 5},
                        {"can_join": False},
                    ],
                }
            ],
        },
        {"key": "river", "nodes": [{"key": "river:market", "rallies": [{"can_join": False}]}]},
        {"key": "forest", "nodes": [{"key": "forest:camp", "rallies": []}]},
    ]
    decorate_map_nodes(maps, {}, [], [], "fiction")
    assert [region["joinable_rally_count"] for region in maps] == [1, 0, 0]


@pytest.mark.django_db
def test_team_slots_keep_returning_escorts_and_legacy_extra_runs_read_only(resource_campaign):
    from gameplay.models import SeasonExpedition
    from gameplay.selectors.season import get_season_context

    season, player, clock = resource_campaign
    member, _ = player()
    for kind, status in (("pve", "returning"), ("transport", "outbound"), ("escort", "returning")):
        SeasonExpedition.objects.create(
            member=member,
            kind=kind,
            status=status,
            node_key="river:docks",
            launched_at=clock[0],
            due_at=clock[0] + timedelta(minutes=30),
        )
    before = list(member.expeditions.values())
    context = get_season_context(member.manor, season.pk)
    attack, defense = context["team_slots"]
    assert (attack["kind"], attack["count"], attack["busy"]) == ("attack", 2, True)
    assert (defense["kind"], defense["count"], defense["busy"]) == ("garrison", 1, True)
    assert defense["first_run"]["kind"] == "escort"
    assert defense["first_run"]["node_key"] == "river:docks"
    assert list(member.expeditions.values()) == before
    assert context["command_guidance"]["main"]["modal"] == "season-team-status"
