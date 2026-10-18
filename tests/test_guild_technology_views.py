from __future__ import annotations

import re

import pytest
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse

from gameplay.services.manor.core import ensure_manor
from guilds.models import Guild, GuildMember, GuildTechnology
from guilds.technology_tree import PUBLIC_TECHNOLOGIES, TECHNOLOGY_NODE_BY_KEY, TECHNOLOGY_ROUTES


@pytest.fixture
def guild_tech_client(django_user_model):
    user = django_user_model.objects.create_user(username="gtech_view_leader", password="pass12345")
    ensure_manor(user)
    guild = Guild.objects.create(name="科技视图帮", founder=user, is_active=True)
    GuildMember.objects.create(guild=guild, user=user, position="leader")

    client = Client()
    assert client.login(username="gtech_view_leader", password="pass12345")
    return client, user, guild


@pytest.mark.django_db
def test_technology_page_renders_public_capacity_effects_and_costs(guild_tech_client):
    client, _user, guild = guild_tech_client
    GuildTechnology.objects.create(
        guild=guild, tech_key="guild_lineup_capacity", category="public", level=0, max_level=20
    )
    GuildTechnology.objects.create(
        guild=guild, tech_key="guild_dispatch_capacity", category="public", level=1, max_level=20
    )

    response = client.get(reverse("guilds:technology"))

    public_nodes = {node["key"]: node for node in response.context["public_technologies"]}
    content = response.content.decode("utf-8")
    assert response.status_code == 200
    assert public_nodes["guild_lineup_capacity"]["current_effect"] == "20 名"
    assert public_nodes["guild_dispatch_capacity"]["current_effect"] == "6 名"
    assert public_nodes["guild_dispatch_capacity"]["next_effect"] == "7 名"
    assert "红宝石×5" in content
    assert "金条×4" in content
    assert 'data-route-tab="public"' in content


@pytest.mark.parametrize(
    ("tech_key", "level", "expected"),
    [
        ("guild_lineup_capacity", 1, "21 名"),
        ("guild_dispatch_capacity", 1, "6 名"),
        ("wolf_force", 10, "嗜血铁骑攻击 +100%"),
    ],
)
def test_level_effect_uses_tree_definition(tech_key, level, expected):
    from guilds.views.technology import _format_level_effect

    assert _format_level_effect(TECHNOLOGY_NODE_BY_KEY[tech_key], level) == expected


@pytest.mark.django_db
def test_technology_page_builds_the_complete_route_and_public_tree(guild_tech_client):
    client, _user, guild = guild_tech_client
    GuildTechnology.objects.create(guild=guild, tech_key="forging_mechanism_blueprint", category="forging", level=1)

    response = client.get(reverse("guilds:technology"))

    routes = {route["key"]: route for route in response.context["technology_routes"]}
    assert response.status_code == 200
    assert set(routes) == {route["key"] for route in TECHNOLOGY_ROUTES}
    for route_definition in TECHNOLOGY_ROUTES:
        rendered_route = routes[route_definition["key"]]
        assert {node["key"] for node in rendered_route["nodes"]} == {node["key"] for node in route_definition["nodes"]}
    assert {node["key"] for node in response.context["public_technologies"]} == {
        node["key"] for node in PUBLIC_TECHNOLOGIES
    }


@pytest.mark.django_db
def test_technology_page_uses_active_route_and_prerequisites_for_upgrade_actions(guild_tech_client):
    client, _user, guild = guild_tech_client
    guild.active_technology_route = "forging"
    guild.save(update_fields=["active_technology_route"])
    GuildTechnology.objects.create(guild=guild, tech_key="forging_mechanism_blueprint", category="forging", level=1)
    GuildTechnology.objects.create(guild=guild, tech_key="forging_equipment_forge", category="forging", level=0)

    response = client.get(reverse("guilds:technology"))

    forging = next(route for route in response.context["technology_routes"] if route["key"] == "forging")
    node = next(node for node in forging["nodes"] if node["key"] == "forging_equipment_forge")
    content = response.content.decode("utf-8")
    assert response.status_code == 200
    assert forging["active"] is True
    assert node["unlocked"] is True and node["available"] is True
    assert f'action="{reverse("guilds:upgrade_tech", args=["forging_equipment_forge"])}"' in content
    assert 'class="tw-tech-action-form"' in content


@pytest.mark.django_db
def test_technology_page_shows_reset_control_for_active_incomplete_route(guild_tech_client):
    client, _user, guild = guild_tech_client
    guild.active_technology_route = "hero"
    guild.save(update_fields=["active_technology_route"])
    GuildTechnology.objects.create(guild=guild, tech_key="hero_fruit_blueprint", category="hero", level=1)

    response = client.get(reverse("guilds:technology"))

    hero = next(route for route in response.context["technology_routes"] if route["key"] == "hero")
    content = response.content.decode("utf-8")
    assert response.status_code == 200
    assert hero["resettable"] is True
    assert f'action="{reverse("guilds:reset_technology_route", args=["hero"])}"' in content
    assert 'aria-label="取消英雄系路线"' in content
    title_row = re.search(r'<div class="tw-tech-route-title-row">(.*?)</div>\s*<p>', content, re.DOTALL)
    assert title_row is not None
    assert "data-route-reset-form" in title_row.group(1)


@pytest.mark.django_db
def test_guild_upgrade_form_posts_with_a_valid_csrf_cookie_and_token(guild_tech_client, monkeypatch):
    _default_client, user, guild = guild_tech_client
    client = Client(enforce_csrf_checks=True)
    assert client.login(username=user.username, password="pass12345")
    monkeypatch.setattr("guilds.views.membership._upgrade_guild_and_get_level", lambda *_args: 2)

    detail_url = reverse("guilds:detail", kwargs={"guild_id": guild.pk})
    page = client.get(detail_url)
    token_match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode("utf-8"))

    assert page.status_code == 200
    assert "csrftoken" in client.cookies
    assert token_match is not None

    response = client.post(
        reverse("guilds:upgrade"),
        {"csrfmiddlewaretoken": token_match.group(1)},
        HTTP_REFERER=f"http://testserver{detail_url}",
    )

    assert response.status_code == 302
    assert response.url == detail_url


@pytest.mark.django_db
def test_technology_page_renders_current_production_outputs(guild_tech_client):
    client, _user, guild = guild_tech_client
    GuildTechnology.objects.create(guild=guild, tech_key="wolf_soul_container", category="wolf", level=3, max_level=3)

    response = client.get(reverse("guilds:technology"))

    wolf = next(route for route in response.context["technology_routes"] if route["key"] == "wolf")
    soul_container = next(node for node in wolf["nodes"] if node["key"] == "wolf_soul_container")
    assert response.status_code == 200
    assert soul_container["daily_output"] == "灵魂容器 ×3"
    assert "灵魂容器 ×3" in response.content.decode("utf-8")


@pytest.mark.django_db
def test_technology_page_uses_runtime_description_config(guild_tech_client, monkeypatch):
    from guilds import constants as guild_constants

    client, _user, _guild = guild_tech_client
    monkeypatch.setitem(guild_constants.TECH_DESCRIPTIONS, "wolf_soul_container", "运行时配置的科技简介")

    response = client.get(reverse("guilds:technology"))

    assert response.status_code == 200
    assert "运行时配置的科技简介" in response.content.decode("utf-8")


@pytest.mark.django_db
def test_technology_page_hides_removed_legacy_technology(guild_tech_client):
    client, _user, guild = guild_tech_client
    GuildTechnology.objects.create(guild=guild, tech_key="military_study", category="public", level=3, max_level=5)

    response = client.get(reverse("guilds:technology"))

    content = response.content.decode("utf-8")
    assert response.status_code == 200
    assert "兵法研习" not in content
    assert "提升门客攻击力" not in content


@pytest.mark.django_db
def test_technology_page_projects_legacy_troop_tactics_to_runtime_max(guild_tech_client):
    client, _user, guild = guild_tech_client
    GuildTechnology.objects.create(guild=guild, tech_key="troop_tactics", category="public", level=5, max_level=5)

    response = client.get(reverse("guilds:technology"))

    public_nodes = {node["key"]: node for node in response.context["public_technologies"]}
    troop_tactics = public_nodes["troop_tactics"]
    assert response.status_code == 200
    assert troop_tactics["level"] == 5 and troop_tactics["max_level"] == 10
    assert troop_tactics["current_effect"] == "帮会战斗中强化护院兵种能力（5 / 10级）"
    assert troop_tactics["next_effect"] == "帮会战斗中强化护院兵种能力（6 / 10级）"


@pytest.mark.django_db
def test_upgrade_technology_redirects_to_tree(guild_tech_client, monkeypatch):
    client, _user, _guild = guild_tech_client
    monkeypatch.setattr("guilds.views.technology.technology_service.upgrade_technology", lambda *_a, **_k: None)

    response = client.post(reverse("guilds:upgrade_tech", kwargs={"tech_key": "troop_tactics"}))

    assert response.status_code == 302
    assert response.url == reverse("guilds:technology")


@pytest.mark.django_db
def test_upgrade_technology_success_message_uses_runtime_tech_names(guild_tech_client, monkeypatch):
    client, _user, _guild = guild_tech_client

    monkeypatch.setitem(
        __import__("guilds.constants", fromlist=["TECH_NAMES"]).TECH_NAMES, "guild_lineup_capacity", "运行时容量科技"
    )
    monkeypatch.setattr("guilds.views.technology.technology_service.upgrade_technology", lambda *_a, **_k: None)

    response = client.post(reverse("guilds:upgrade_tech", kwargs={"tech_key": "guild_lineup_capacity"}), follow=True)

    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert response.redirect_chain
    assert messages[-1] == "运行时容量科技升级成功！"
