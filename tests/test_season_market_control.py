"""集市夺取、优惠、查询与现有赛季追加配置的集成验证。"""

from copy import deepcopy
from importlib import import_module
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.apps import apps
from django.db import connection

from gameplay.models import Season, SeasonExpedition, SeasonRally
from gameplay.selectors.season import get_season_context
from gameplay.services.season.actions import perform_action
from gameplay.services.season.campaign_rules import validate_campaign_rules
from gameplay.services.season.expeditions import process_expedition
from gameplay.services.season.market import market_offers, market_return_percent
from gameplay.services.season.warfare import campaign_nodes
from tests.test_season_simplification import _resource_campaign as _resource_campaign  # noqa: F401
from tests.test_season_simplification import simple_world as _simple_world  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(request):
    return request.getfixturevalue("_simple_world")


def test_market_can_be_captured_by_real_siege_rally(world):
    season, player, clock = world
    member, guest = player()
    state = season.faction_states.get(faction=member.faction)
    state.troops["dao_sheng"] = 1000
    state.save(update_fields=["troops"])
    season.rules["campaign"]["sites"]["river:market"]["control_required"] = 25
    season.save(update_fields=["rules"])
    site = season.sites.get(key="river:market")
    site.guards, site.durability = 1, 25
    site.save(update_fields=["guards", "durability"])
    response = perform_action(
        member.manor,
        season.pk,
        "rally_create",
        str(uuid4()),
        {
            "node_key": site.key,
            "mode": "siege",
            "guest_ids": [guest.pk],
            "troops": {"dao_sheng": 100},
            "rally_target_members": 1,
        },
    )
    rally = SeasonRally.objects.get(pk=response["rally_id"])
    run = SeasonExpedition.objects.get(pk=response["run_id"])
    for _ in range(8):
        rally.refresh_from_db()
        if rally.status == "resolved":
            break
        clock[0] = rally.due_at
        assert process_expedition(run.pk, clock[0])
    site.refresh_from_db()
    assert site.owner == member.faction
    assert campaign_nodes(season)[site.key]["kind"] == "pvp"
    assert market_return_percent(season, member.faction) == 85


def test_discount_follows_actual_ownership_without_personal_reward(world):
    season, player, clock = world
    member, _ = player()
    state = season.faction_states.get(faction=member.faction)
    state.resources = {"grain": 100000, "wood": 0, "iron": 0}
    state.save(update_fields=["resources"])
    site = season.sites.get(key="river:market")
    site.owner = member.faction
    site.save(update_fields=["owner"])
    result = perform_action(
        member.manor,
        season.pk,
        "market_exchange",
        str(uuid4()),
        {
            "source_resource": "grain",
            "target_resource": "iron",
        },
    )
    assert result["cost"] == 200 and result["received"] == 56
    member.refresh_from_db()
    assert member.merit == member.coins == 0
    site.owner = "history"
    site.save(update_fields=["owner"])
    assert market_return_percent(season, member.faction) == 80
    state.refresh_from_db()
    quote = next(
        row
        for row in market_offers(season, state, clock[0])["offers"]
        if row["source"] == "grain" and row["target"] == "iron"
    )
    assert quote["received"] == 53


def test_market_selector_is_read_only_and_ordinary_shop_remains_available(world):
    season, player, _ = world
    member, _ = player()
    before = list(season.sites.values())
    context = get_season_context(member.manor, season.pk)
    market_node = next(
        node for region in context["season_maps"] for node in region["nodes"] if node["key"] == "river:market"
    )
    assert market_node["occupation_label"] == "中立 · 未占领"
    assert "exchange" in {pane["key"] for pane in market_node["popup_panes"]}
    assert context["market_stock"]["enabled"]
    assert context["market_stock"]["offers"] == []
    assert not context["market_stock"]["is_owner"]
    assert context["shop_items"]
    assert not member.claims.exists()
    assert list(season.sites.values()) == before


def test_market_exchange_remains_visible_when_opponent_controls_market(world):
    season, player, _ = world
    member, _ = player()
    site = season.sites.get(key="river:market")
    site.owner = "history" if member.faction == "fiction" else "fiction"
    site.save(update_fields=["owner"])
    context = get_season_context(member.manor, season.pk)
    node = next(node for region in context["season_maps"] for node in region["nodes"] if node["key"] == site.key)
    panes = {pane["key"] for pane in node["popup_panes"]}
    assert "exchange" in panes
    assert "trade" not in panes


def test_upgrade_adds_only_market_rules_and_preserves_existing_campaign_progress(world):
    season, player, _ = world
    member, _ = player()
    del season.rules["campaign"]["market_control"]
    del season.rules["campaign"]["sites"]["river:market"]
    del season.rules["campaign"]["expeditions"]["enemy_levels"]["river:market"]
    season.save(update_fields=["rules"])
    member.merit, member.coins = 123, 77
    member.save(update_fields=["merit", "coins"])
    before_sites = list(season.sites.values())
    before_states = list(season.faction_states.values())
    before_rules = deepcopy(season.rules)
    migration = import_module("gameplay.migrations.0253_season_market_control")
    migration.enable_market_control(apps, SimpleNamespace(connection=connection))
    season.refresh_from_db()
    validate_campaign_rules(season.rules["campaign"])
    assert season.rules["campaign"]["market_control"]["enabled"]
    assert before_rules["campaign"]["sites"] == {
        key: value for key, value in season.rules["campaign"]["sites"].items() if key != "river:market"
    }
    assert list(season.sites.values()) == before_sites
    assert list(season.faction_states.values()) == before_states
    member.refresh_from_db()
    assert (member.merit, member.coins) == (123, 77)


def test_null_market_config_keeps_legacy_behavior(world):
    season, _, _ = world
    season.rules["campaign"]["market_control"] = None
    validate_campaign_rules(season.rules["campaign"])
    assert market_return_percent(season, "fiction") == 80
    assert market_return_percent(season, "") == 80


def test_upgrade_creates_missing_market_using_preserved_custom_rules(world):
    original, _, clock = world
    rules = deepcopy(original.rules)
    del rules["campaign"]["market_control"]
    rules["campaign"]["sites"]["river:market"]["guards"] = 7
    rules["campaign"]["sites"]["river:market"]["durability"] = 777
    rules["campaign"]["expeditions"]["enemy_levels"]["river:market"] = 22
    season = Season.objects.create(
        name="自定义集市迁移",
        starts_at=original.starts_at,
        ends_at=original.ends_at,
        rules=rules,
        population={"mode": "pioneer"},
    )
    migration = import_module("gameplay.migrations.0253_season_market_control")
    migration.enable_market_control(apps, SimpleNamespace(connection=connection))
    market = season.sites.get(key="river:market")
    assert market.guards == 2 and market.durability == 264
    assert market.npc_profile["level"] == 13
    assert market.last_score_at >= clock[0]
    migration.enable_market_control(apps, SimpleNamespace(connection=connection))
    assert season.sites.count() == 1
