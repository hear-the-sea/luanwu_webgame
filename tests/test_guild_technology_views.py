from __future__ import annotations

import pytest
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse

from gameplay.services.manor.core import ensure_manor
from guilds.models import Guild, GuildMember, GuildTechnology


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
def test_technology_page_renders_capacity_tech_with_count_effect_and_ruby_cost(guild_tech_client):
    client, _user, guild = guild_tech_client
    GuildTechnology.objects.create(
        guild=guild, tech_key="guild_lineup_capacity", category="combat", level=0, max_level=20
    )
    GuildTechnology.objects.create(
        guild=guild, tech_key="guild_dispatch_capacity", category="combat", level=1, max_level=20
    )

    response = client.get(reverse("guilds:technology"))

    content = response.content.decode("utf-8")
    assert response.status_code == 200
    assert "提升帮会已上阵名单总容量" in content
    assert "提升单次帮会任务最多可派出的门客人数" in content
    assert "20 名" in content
    assert "6 名" in content
    assert "红宝石 x1" in content


@pytest.mark.django_db
def test_technology_page_uses_category_switches_and_removes_caption(guild_tech_client):
    client, _user, guild = guild_tech_client
    GuildTechnology.objects.create(
        guild=guild, tech_key="equipment_forge", category="production", level=1, max_level=10
    )
    GuildTechnology.objects.create(guild=guild, tech_key="troop_tactics", category="combat", level=1, max_level=10)
    GuildTechnology.objects.create(guild=guild, tech_key="resource_boost", category="welfare", level=1, max_level=5)

    response = client.get(reverse("guilds:technology"))

    content = response.content.decode("utf-8")
    assert response.status_code == 200
    assert "科技视图帮 · 提升全员生产战斗能力" not in content
    assert "?category=production" in content
    assert "?category=combat" in content
    assert "?category=welfare" in content
    assert "生产类科技" in content
    assert "战斗类科技" in content
    assert "福利类科技" in content
    assert "每日生产装备道具" in content
    assert "帮会科技一发功，兵种科技就跟着胡乱长个儿" not in content
    assert "提升庄园资源产出" not in content


@pytest.mark.django_db
def test_technology_page_filters_to_selected_category(guild_tech_client):
    client, _user, guild = guild_tech_client
    GuildTechnology.objects.create(
        guild=guild, tech_key="equipment_forge", category="production", level=1, max_level=10
    )
    GuildTechnology.objects.create(guild=guild, tech_key="troop_tactics", category="combat", level=1, max_level=10)
    GuildTechnology.objects.create(guild=guild, tech_key="resource_boost", category="welfare", level=1, max_level=5)

    response = client.get(reverse("guilds:technology") + "?category=combat")

    content = response.content.decode("utf-8")
    assert response.status_code == 200
    assert "帮会科技一发功，兵种科技就跟着胡乱长个儿" in content
    assert "每日生产装备道具" not in content
    assert "提升庄园资源产出" not in content


@pytest.mark.django_db
def test_technology_page_uses_unified_card_style_without_section_title(guild_tech_client):
    client, _user, guild = guild_tech_client
    GuildTechnology.objects.create(guild=guild, tech_key="troop_tactics", category="combat", level=1, max_level=10)

    response = client.get(reverse("guilds:technology") + "?category=combat")

    content = response.content.decode("utf-8")
    assert response.status_code == 200
    assert "<h2>生产类科技</h2>" not in content
    assert "<h2>战斗类科技</h2>" not in content
    assert "<h2>福利类科技</h2>" not in content
    assert 'class="building-grid"' not in content
    assert 'class="building-card"' not in content
    assert 'class="tw-building-grid"' in content
    assert 'class="tw-building-card"' in content


@pytest.mark.django_db
def test_technology_upgrade_action_is_centered_with_spacing(guild_tech_client):
    client, _user, guild = guild_tech_client
    GuildTechnology.objects.create(
        guild=guild, tech_key="equipment_forge", category="production", level=1, max_level=10
    )

    response = client.get(reverse("guilds:technology") + "?category=production")

    content = response.content.decode("utf-8")
    assert response.status_code == 200
    assert 'class="tw-card-actions tw-guild-tech-actions"' in content
    assert 'class="tw-btn-primary tw-guild-tech-action-button"' in content
    assert 'action="/guilds/technology/equipment_forge/upgrade/?category=production"' in content
    assert content.index('class="tw-card-actions tw-guild-tech-actions"') > content.index('class="tw-guild-dl"')
    assert "升级" in content


@pytest.mark.django_db
def test_upgrade_technology_redirect_preserves_selected_category(guild_tech_client, monkeypatch):
    client, _user, _guild = guild_tech_client

    monkeypatch.setattr("guilds.views.technology.technology_service.upgrade_technology", lambda *_a, **_k: None)

    response = client.post(reverse("guilds:upgrade_tech", kwargs={"tech_key": "troop_tactics"}) + "?category=combat")

    assert response.status_code == 302
    assert response.url == reverse("guilds:technology") + "?category=combat"


@pytest.mark.django_db
def test_technology_page_hides_removed_military_study_even_if_legacy_row_exists(guild_tech_client):
    client, _user, guild = guild_tech_client
    GuildTechnology.objects.create(guild=guild, tech_key="military_study", category="combat", level=3, max_level=5)

    response = client.get(reverse("guilds:technology"))

    content = response.content.decode("utf-8")
    assert response.status_code == 200
    assert "兵法研习" not in content
    assert "提升门客攻击力" not in content
    assert "武力 +6%，智力 +2%，防御 +0%" not in content


@pytest.mark.django_db
def test_technology_page_projects_legacy_troop_tactics_to_runtime_max_and_mapping_copy(guild_tech_client):
    client, _user, guild = guild_tech_client
    GuildTechnology.objects.create(guild=guild, tech_key="troop_tactics", category="combat", level=5, max_level=5)

    response = client.get(reverse("guilds:technology"))

    content = response.content.decode("utf-8")
    assert response.status_code == 200
    assert "帮会科技一发功，兵种科技就跟着胡乱长个儿" in content
    assert "5 / 10" in content
    assert "按 5 / 10 级线性映射个人兵种科技" in content
    assert "按 6 / 10 级线性映射个人兵种科技" in content


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
