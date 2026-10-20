from __future__ import annotations

import re

import pytest
from django.test import Client
from django.urls import reverse

from core.exceptions import GuildTechnologyError
from gameplay.services.manor.core import ensure_manor
from guilds.models import Guild, GuildMember, GuildResourceLog, GuildTechnology, GuildWarehouse
from guilds.services.technology import select_technology_route, upgrade_technology
from guilds.services.warehouse import get_technology_daily_output_display, produce_technology_items
from guilds.services.warehouse_config import get_technology_production_items
from guilds.tasks import process_single_guild_production
from guilds.technology_tree import PRODUCTION_TECH_KEYS, TECHNOLOGY_ROUTES

pytestmark = pytest.mark.django_db
DISABLED_KEY = "hero_blue_purple_pill"
HERO_ROUTE = next(route for route in TECHNOLOGY_ROUTES if route["key"] == "hero")


@pytest.fixture
def technology_world(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="technology_availability")
    ensure_manor(user)
    guild = Guild.objects.create(
        name="科技开放条件", founder=user, silver=1000000000, is_active=True, active_technology_route="hero"
    )
    member = GuildMember.objects.create(guild=guild, user=user, position="leader", is_active=True)
    monkeypatch.setattr("guilds.services.technology.create_announcement", lambda *args, **kwargs: None)
    return guild, member


def seed_hero_route(guild, *, unfinished_key=None):
    for node in HERO_ROUTE["nodes"]:
        level = node["max_level"] if node["enabled"] else 0
        if node["key"] == unfinished_key:
            level -= 1
        GuildTechnology.objects.create(guild=guild, tech_key=node["key"], level=level, max_level=node["max_level"])


@pytest.mark.parametrize("level", [0, 1, 3])
def test_disabled_technology_rejects_upgrade_without_charging(technology_world, level):
    guild, member = technology_world
    tech = GuildTechnology.objects.create(guild=guild, tech_key=DISABLED_KEY, level=level, max_level=3)
    with pytest.raises(GuildTechnologyError, match="暂未开放"):
        upgrade_technology(guild, DISABLED_KEY, member.user)
    guild.refresh_from_db()
    tech.refresh_from_db()
    assert tech.level == level
    assert guild.silver == 1000000000
    assert not GuildResourceLog.objects.filter(guild=guild).exists()


def test_disabled_existing_technology_does_not_produce_through_any_entry(technology_world):
    guild, _member = technology_world
    tech = GuildTechnology.objects.create(guild=guild, tech_key=DISABLED_KEY, level=3, max_level=3)
    assert DISABLED_KEY not in PRODUCTION_TECH_KEYS
    assert get_technology_production_items(DISABLED_KEY, 3) == []
    assert get_technology_daily_output_display(DISABLED_KEY, 3) is None
    produce_technology_items(guild, DISABLED_KEY, 3)
    process_single_guild_production.run(guild.pk)
    assert not GuildWarehouse.objects.filter(guild=guild).exists()
    tech.refresh_from_db()
    assert tech.last_production_at is None


def test_last_open_hero_upgrade_completes_route_without_disabled_node(technology_world):
    guild, member = technology_world
    seed_hero_route(guild, unfinished_key="hero_skill_books")
    GuildWarehouse.objects.create(guild=guild, item_key="gold_bar", quantity=10000000, contribution_cost=50)
    upgrade_technology(guild, "hero_skill_books", member.user)
    guild.refresh_from_db()
    assert guild.active_technology_route == ""
    assert guild.completed_technology_routes == ["hero"]
    assert GuildTechnology.objects.get(guild=guild, tech_key=DISABLED_KEY).level == 0
    select_technology_route(guild, "wolf", member.user)
    guild.refresh_from_db()
    assert guild.active_technology_route == "wolf"


@pytest.mark.parametrize("position", ["leader", "admin"])
def test_already_maxed_open_nodes_can_switch_without_an_extra_upgrade(technology_world, position):
    guild, member = technology_world
    member.position = position
    member.save(update_fields=["position"])
    seed_hero_route(guild)
    select_technology_route(guild, "wolf", member.user)
    guild.refresh_from_db()
    assert guild.active_technology_route == "wolf"
    assert guild.completed_technology_routes == ["hero"]


def test_other_unfinished_open_nodes_still_prevent_switching(technology_world):
    guild, member = technology_world
    seed_hero_route(guild, unfinished_key="hero_skill_books")
    with pytest.raises(GuildTechnologyError, match="开放节点尚未全部研究完成"):
        select_technology_route(guild, "wolf", member.user)
    guild.refresh_from_db()
    assert guild.active_technology_route == "hero"
    assert guild.completed_technology_routes == []


def test_member_cannot_switch_even_when_all_open_nodes_are_maxed(technology_world):
    guild, member = technology_world
    seed_hero_route(guild)
    member.position = "member"
    member.save(update_fields=["position"])
    with pytest.raises(GuildTechnologyError, match="只有帮主和管理员"):
        select_technology_route(guild, "wolf", member.user)
    guild.refresh_from_db()
    assert guild.active_technology_route == "hero"


def test_route_switch_rechecks_manager_permission_inside_transaction(technology_world, monkeypatch):
    from guilds.services import technology as service

    guild, member = technology_world
    seed_hero_route(guild)
    original = service.get_active_membership

    def demote_after_initial_check(*args, **kwargs):
        membership = original(*args, **kwargs)
        GuildMember.objects.filter(pk=member.pk).update(position="member")
        return membership

    monkeypatch.setattr(service, "get_active_membership", demote_after_initial_check)
    with pytest.raises(GuildTechnologyError, match="只有帮主和管理员"):
        select_technology_route(guild, "wolf", member.user)
    guild.refresh_from_db()
    assert guild.active_technology_route == "hero"
    assert guild.completed_technology_routes == []


def test_page_disables_blue_purple_and_offers_next_route_for_existing_guild(technology_world, client):
    guild, member = technology_world
    seed_hero_route(guild)
    client.force_login(member.user)
    response = client.get(reverse("guilds:technology"))
    assert response.status_code == 200
    routes = {route["key"]: route for route in response.context["technology_routes"]}
    node = next(node for node in routes["hero"]["nodes"] if node["key"] == DISABLED_KEY)
    assert not node["enabled"] and not node["available"]
    assert node["current_effect"] == "暂未开放" and node["daily_output"] is None
    assert routes["hero"]["ready_to_complete"]
    assert routes["wolf"]["selectable"]
    content = response.content.decode()
    assert reverse("guilds:upgrade_tech", args=[DISABLED_KEY]) not in content
    assert reverse("guilds:select_technology_route", args=["wolf"]) in content
    assert "暂未开放的节点不计入完成条件" in content


def test_equipment_forge_unlocks_mechanism_blueprint(technology_world):
    guild, member = technology_world
    equipment_forge = GuildTechnology.objects.create(
        guild=guild, tech_key="forging_equipment_forge", level=0, max_level=10
    )
    mechanism_blueprint = GuildTechnology.objects.create(
        guild=guild, tech_key="forging_mechanism_blueprint", level=0, max_level=5
    )
    gold = GuildWarehouse.objects.create(guild=guild, item_key="gold_bar", quantity=100, contribution_cost=1200)
    ruby = GuildWarehouse.objects.create(guild=guild, item_key="red_ruby", quantity=100, contribution_cost=1200)
    with pytest.raises(GuildTechnologyError, match="请先选择并开启该科技路线"):
        upgrade_technology(guild, "forging_equipment_forge", member.user)
    Guild.objects.filter(pk=guild.pk).update(active_technology_route="forging")
    with pytest.raises(GuildTechnologyError, match="前置科技未达到1级"):
        upgrade_technology(guild, "forging_mechanism_blueprint", member.user)
    gold.refresh_from_db()
    ruby.refresh_from_db()
    assert gold.quantity == 100
    upgrade_technology(guild, "forging_equipment_forge", member.user)
    gold.refresh_from_db()
    equipment_forge.refresh_from_db()
    assert gold.quantity == 97
    assert equipment_forge.level == 1
    upgrade_technology(guild, "forging_mechanism_blueprint", member.user)
    gold.refresh_from_db()
    ruby.refresh_from_db()
    mechanism_blueprint.refresh_from_db()
    assert gold.quantity == 92
    assert ruby.quantity == 95
    assert mechanism_blueprint.level == 1


def test_equipment_forge_upgrade_form_submits_csrf_token(technology_world):
    guild, member = technology_world
    Guild.objects.filter(pk=guild.pk).update(active_technology_route="forging")
    GuildTechnology.objects.create(guild=guild, tech_key="forging_equipment_forge", level=0, max_level=10)
    GuildWarehouse.objects.create(guild=guild, item_key="gold_bar", quantity=100, contribution_cost=1200)

    client = Client(enforce_csrf_checks=True)
    client.force_login(member.user)
    page = client.get(reverse("guilds:technology"))
    upgrade_url = reverse("guilds:upgrade_tech", args=["forging_equipment_forge"])
    upgrade_form = re.search(
        rf'<form\b[^>]*action="{re.escape(upgrade_url)}"[^>]*>(.*?)</form>',
        page.content.decode(),
        re.DOTALL,
    )
    assert upgrade_form is not None
    token = re.search(r'name="csrfmiddlewaretoken"\s+value="([^"]+)"', upgrade_form.group(1))
    assert token is not None

    response = client.post(upgrade_url, {"csrfmiddlewaretoken": token.group(1)})

    assert response.status_code == 302
    assert GuildTechnology.objects.get(guild=guild, tech_key="forging_equipment_forge").level == 1


def test_equipment_forge_migration_reopens_completed_route_without_resetting_progress(technology_world):
    from importlib import import_module
    from types import SimpleNamespace

    from django.apps import apps
    from django.db import connection

    guild, _member = technology_world
    Guild.objects.filter(pk=guild.pk).update(
        completed_technology_routes=["hero", "forging"], active_technology_route="wolf"
    )
    blueprint = GuildTechnology.objects.create(
        guild=guild, tech_key="forging_mechanism_blueprint", level=5, max_level=5
    )
    migration = import_module("guilds.migrations.0035_forging_equipment_forge")
    migration.add_equipment_forge(apps, SimpleNamespace(connection=connection))
    tech = GuildTechnology.objects.get(guild=guild, tech_key="forging_equipment_forge")
    assert (tech.level, tech.max_level, tech.category) == (0, 10, "forging")
    guild.refresh_from_db()
    assert guild.completed_technology_routes == ["hero"]
    assert guild.active_technology_route == "wolf"
    blueprint.refresh_from_db()
    assert blueprint.level == 5
    tech.level = 4
    tech.save(update_fields=["level"])
    migration.add_equipment_forge(apps, SimpleNamespace(connection=connection))
    tech.refresh_from_db()
    assert tech.level == 4


def test_equipment_forge_is_in_the_forging_tree_and_shows_daily_boxes(technology_world, client):
    guild, member = technology_world
    GuildTechnology.objects.create(guild=guild, tech_key="forging_equipment_forge", level=10, max_level=10)
    client.force_login(member.user)
    response = client.get(reverse("guilds:technology"))
    assert response.status_code == 200
    forging = next(route for route in response.context["technology_routes"] if route["key"] == "forging")
    node = next(node for node in forging["nodes"] if node["key"] == "forging_equipment_forge")
    blueprint = next(node for node in forging["nodes"] if node["key"] == "forging_mechanism_blueprint")
    node_keys = [node["key"] for node in forging["nodes"]]
    tactical_cards = {
        node["key"]: node
        for node in forging["nodes"]
        if node["key"] in {"forging_slave_card", "forging_theft_card", "forging_injury_card"}
    }
    assert node["max_level"] == 10
    assert node["requires"] == ()
    assert blueprint["requires"] == ("forging_equipment_forge",)
    assert node_keys.index("forging_equipment_forge") < node_keys.index("forging_mechanism_blueprint")
    assert set(tactical_cards) == {"forging_slave_card", "forging_theft_card", "forging_injury_card"}
    assert all(card["requires"] == ("forging_equipment_forge",) for card in tactical_cards.values())
    assert "帮会宗师秘匣 ×2" in node["daily_output"]
    assert "帮会精铸紫装箱 ×2" in node["daily_output"]


def test_sheep_wrath_node_hides_redeemable_item_details(technology_world, client):
    guild, member = technology_world
    GuildTechnology.objects.create(guild=guild, tech_key="sheep_wrath", level=1, max_level=1)
    client.force_login(member.user)

    response = client.get(reverse("guilds:technology"))

    content = response.content.decode("utf-8")
    assert response.status_code == 200
    assert "帮会资产道具 · 羊的愤怒" not in content
    assert "每 5 天产出 1 个" not in content
    assert content.count("guild_sheep_wrath.webp") == 1
    assert "5000 贡献兑换" not in content
    assert "10 根金条" not in content
    assert "每位成员每 7 天" not in content
