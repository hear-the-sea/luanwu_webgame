from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from battle.models import TroopTemplate
from gameplay.models import PlayerTroop
from gameplay.services.manor.core import ensure_manor
from guilds.constants import DAILY_DONATION_LIMITS
from guilds.models import Guild, GuildMember, GuildTroopStorage, GuildWarehouse


@pytest.fixture
def guild_member_client(django_user_model):
    user = django_user_model.objects.create_user(username="resource_view_user", password="pass12345")
    manor = ensure_manor(user)
    guild = Guild.objects.create(
        name="资源展示帮会",
        founder=user,
        is_active=True,
        silver=1234,
        grain=567,
        gold_bar=8,
    )
    member = GuildMember.objects.create(guild=guild, user=user, position="member", is_active=True)

    client = Client()
    assert client.login(username="resource_view_user", password="pass12345")
    return client, guild, member, manor


@pytest.mark.django_db
def test_resources_page_shows_red_ruby_and_troop_overview(guild_member_client):
    client, guild, _member, _manor = guild_member_client

    GuildWarehouse.objects.create(guild=guild, item_key="red_ruby", quantity=12, contribution_cost=0)

    spear = TroopTemplate.objects.create(key="rv_spear", name="长枪护院")
    archer = TroopTemplate.objects.create(key="rv_archer", name="弓弩护院")
    GuildTroopStorage.objects.create(guild=guild, troop_template=spear, count=9)
    GuildTroopStorage.objects.create(guild=guild, troop_template=archer, count=3)

    response = client.get(reverse("guilds:resources"))

    assert response.status_code == 200
    assert response.context["red_ruby_count"] == 12
    assert response.context["troop_overview"]["total_count"] == 12
    assert response.context["troop_overview"]["kinds_count"] == 2

    content = response.content.decode("utf-8")
    assert "红宝石" in content
    assert "护院总数" in content
    assert "护院种类" in content
    assert "长枪护院" in content
    assert "弓弩护院" in content
    assert "帮会护院明细" in content
    assert "模板标识：rv_spear" in content
    assert "库存 9" in content


@pytest.mark.django_db
def test_resources_page_has_unified_four_donation_entries(guild_member_client):
    client, guild, member, manor = guild_member_client
    GuildWarehouse.objects.create(guild=guild, item_key="red_ruby", quantity=1, contribution_cost=0)
    manor.silver = 23
    manor.grain = 40
    manor.save(update_fields=["silver", "grain"])
    member.daily_donation_grain = max(0, int(DAILY_DONATION_LIMITS["grain"]) - 3)
    member.save(update_fields=["daily_donation_grain"])

    player_troop_template = TroopTemplate.objects.create(key="my_archer", name="我的弓兵")
    guild_troop_template = TroopTemplate.objects.create(key="guild_only_guard", name="库存护院")
    PlayerTroop.objects.create(manor=manor, troop_template=player_troop_template, count=6)
    GuildTroopStorage.objects.create(guild=guild, troop_template=guild_troop_template, count=9)

    response = client.get(reverse("guilds:resources"))

    assert response.status_code == 200
    assert response.context["donation_entries"]["silver"]["max_amount"] == 23
    assert response.context["donation_entries"]["grain"]["max_amount"] == 3
    body = response.content.decode("utf-8")
    assert "捐赠银两" in body
    assert "捐赠粮食" in body
    assert "捐赠金条" in body
    assert "捐赠护院" in body
    assert f'action="{reverse("guilds:donate_troops")}"' in body
    assert 'name="troop_key"' in body
    assert 'name="quantity"' in body
    assert 'id="silver_amount"' in body and 'max="23"' in body
    assert 'id="grain_amount"' in body and 'max="3"' in body
    assert '<option value="my_archer">' in body
    assert '<option value="guild_only_guard">' not in body


@pytest.mark.django_db
def test_resources_page_resets_silver_and_grain_daily_display_after_day_rollover(guild_member_client):
    client, _guild, member, manor = guild_member_client
    manor.silver = 120
    manor.grain = 90
    manor.save(update_fields=["silver", "grain"])
    member.daily_donation_silver = 33
    member.daily_donation_grain = 22
    member.daily_donation_reset_at = timezone.localdate() - timedelta(days=1)
    member.save(update_fields=["daily_donation_silver", "daily_donation_grain", "daily_donation_reset_at"])

    response = client.get(reverse("guilds:resources"))

    assert response.status_code == 200
    donation_entries = response.context["donation_entries"]
    assert donation_entries["silver"]["donated_today"] == 0
    assert donation_entries["silver"]["remaining_today"] == int(DAILY_DONATION_LIMITS["silver"])
    assert donation_entries["grain"]["donated_today"] == 0
    assert donation_entries["grain"]["remaining_today"] == int(DAILY_DONATION_LIMITS["grain"])


@pytest.mark.django_db
def test_donate_page_remains_accessible_and_uses_integrated_donation_experience(guild_member_client):
    client, _guild, _member, _manor = guild_member_client

    response = client.get(reverse("guilds:donate"))

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "帮会资源" in body
    assert "护院总数" in body
    assert "捐赠银两" in body
    assert "捐赠粮食" in body
    assert "捐赠金条" in body
    assert "捐赠护院" in body


@pytest.mark.django_db
def test_donate_resource_post_redirects_back_to_resources(guild_member_client):
    client, guild, _member, manor = guild_member_client
    manor.silver = 200
    manor.save(update_fields=["silver"])

    response = client.post(
        reverse("guilds:donate"),
        {"resource_type": "silver", "amount": "100"},
        follow=False,
    )

    assert response.status_code == 302
    assert response["Location"].endswith(reverse("guilds:resources"))
