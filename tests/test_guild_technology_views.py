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
def test_technology_page_renders_actual_military_study_formula(guild_tech_client):
    client, _user, guild = guild_tech_client
    GuildTechnology.objects.create(guild=guild, tech_key="military_study", category="combat", level=3, max_level=5)

    response = client.get(reverse("guilds:technology"))

    content = response.content.decode("utf-8")
    assert response.status_code == 200
    assert "武力 +6%，智力 +2%，防御 +0%" in content
    assert "武力 +8%，智力 +4%，防御 +0%" in content


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
