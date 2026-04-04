from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from battle.models import TroopTemplate
from gameplay.models import ItemTemplate, PlayerTroop
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
def test_guild_detail_page_shows_red_ruby_and_troop_overview(guild_member_client):
    client, guild, _member, _manor = guild_member_client

    GuildWarehouse.objects.create(guild=guild, item_key="red_ruby", quantity=12, contribution_cost=0)

    spear = TroopTemplate.objects.create(key="rv_spear", name="长枪护院")
    archer = TroopTemplate.objects.create(key="rv_archer", name="弓弩护院")
    GuildTroopStorage.objects.create(guild=guild, troop_template=spear, count=9)
    GuildTroopStorage.objects.create(guild=guild, troop_template=archer, count=3)

    response = client.get(reverse("guilds:detail", args=[guild.id]))

    assert response.status_code == 200
    assert response.context["troop_overview"]["total_count"] == 12
    assert response.context["troop_overview"]["kinds_count"] == 2

    content = response.content.decode("utf-8")
    assert "帮会资源" in content
    assert "护院" in content
    assert "资源捐赠" in content


@pytest.mark.django_db
def test_guild_detail_page_has_unified_four_donation_entries(guild_member_client):
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

    response = client.get(reverse("guilds:detail", args=[guild.id]))

    assert response.status_code == 200
    assert response.context["donation_entries"]["silver"]["max_amount"] == 23
    assert response.context["donation_entries"]["grain"]["max_amount"] == 3
    body = response.content.decode("utf-8")
    assert 'id="guild-donation-modal"' in body
    assert 'id="guild-donation-kind"' in body
    assert '<option value="silver">' in body
    assert '<option value="grain">' in body
    assert '<option value="gold_bar">' in body
    assert '<option value="troops">' in body
    assert f'action="{reverse("guilds:donate_troops")}"' in body
    assert '<select id="troop_key"' in body
    assert 'name="quantity"' in body
    assert 'id="silver_amount"' in body and 'max="23"' in body
    assert 'id="grain_amount"' in body and 'max="3"' in body
    assert '<option value="my_archer">' in body
    assert '<option value="guild_only_guard">' not in body


@pytest.mark.django_db
def test_guild_detail_page_resets_silver_and_grain_daily_display_after_day_rollover(guild_member_client):
    client, _guild, member, manor = guild_member_client
    manor.silver = 120
    manor.grain = 90
    manor.save(update_fields=["silver", "grain"])
    member.daily_donation_silver = 33
    member.daily_donation_grain = 22
    member.daily_donation_reset_at = timezone.localdate() - timedelta(days=1)
    member.save(update_fields=["daily_donation_silver", "daily_donation_grain", "daily_donation_reset_at"])

    response = client.get(reverse("guilds:detail", args=[_guild.id]))

    assert response.status_code == 200
    donation_entries = response.context["donation_entries"]
    assert donation_entries["silver"]["donated_today"] == 0
    assert donation_entries["silver"]["remaining_today"] == int(DAILY_DONATION_LIMITS["silver"])
    assert donation_entries["grain"]["donated_today"] == 0
    assert donation_entries["grain"]["remaining_today"] == int(DAILY_DONATION_LIMITS["grain"])


@pytest.mark.django_db
def test_guild_detail_page_uses_member_gold_bar_daily_counter(guild_member_client):
    client, guild, member, _manor = guild_member_client
    member.daily_donation_gold_bar = 4
    member.daily_donation_reset_at = timezone.localdate()
    member.save(update_fields=["daily_donation_gold_bar", "daily_donation_reset_at"])

    response = client.get(reverse("guilds:detail", args=[guild.id]))

    assert response.status_code == 200
    donation_entries = response.context["donation_entries"]
    assert donation_entries["gold_bar"]["donated_today"] == 4
    assert donation_entries["gold_bar"]["remaining_today"] == int(DAILY_DONATION_LIMITS["gold_bar"]) - 4


@pytest.mark.django_db
def test_guild_detail_page_reads_latest_runtime_contribution_rules(guild_member_client, monkeypatch):
    client, guild, _member, manor = guild_member_client
    manor.silver = 23
    manor.grain = 40
    manor.save(update_fields=["silver", "grain"])
    monkeypatch.setattr("guilds.constants.CONTRIBUTION_RATES", {"silver": 9, "grain": 8, "gold_bar": 7})
    monkeypatch.setattr("guilds.constants.DAILY_DONATION_LIMITS", {"silver": 5, "grain": 7, "gold_bar": 2})

    response = client.get(reverse("guilds:detail", args=[guild.id]))

    assert response.status_code == 200
    donation_entries = response.context["donation_entries"]
    assert donation_entries["silver"]["rate"] == 9
    assert donation_entries["silver"]["daily_limit"] == 5
    assert donation_entries["grain"]["rate"] == 8
    assert donation_entries["grain"]["daily_limit"] == 7
    assert donation_entries["gold_bar"]["rate"] == 7
    assert donation_entries["gold_bar"]["daily_limit"] == 2


@pytest.mark.django_db
def test_resources_page_is_removed(guild_member_client):
    client, _guild, _member, _manor = guild_member_client

    response = client.get("/guilds/resources/")

    assert response.status_code == 404


@pytest.mark.django_db
def test_guild_warehouse_page_projects_guild_resources_without_writing_guild_warehouse(guild_member_client):
    client, guild, _member, _manor = guild_member_client
    ItemTemplate.objects.get_or_create(key="grain", defaults={"name": "粮食"})
    ItemTemplate.objects.get_or_create(key="gold_bar", defaults={"name": "金条"})
    GuildWarehouse.objects.filter(guild=guild, item_key__in=["silver", "grain", "gold_bar"]).delete()

    response = client.get(reverse("guilds:warehouse"))

    assert response.status_code == 200
    assert GuildWarehouse.objects.filter(guild=guild, item_key__in=["silver", "grain", "gold_bar"]).exists() is False

    projected_entries = {entry.item_key: entry for entry in response.context["warehouse_items"]}
    assert projected_entries["silver"].display_quantity == guild.silver
    assert projected_entries["silver"].is_projected is True
    assert projected_entries["grain"].display_quantity == guild.grain
    assert projected_entries["grain"].is_projected is True
    assert projected_entries["gold_bar"].display_quantity == guild.gold_bar
    assert projected_entries["gold_bar"].is_projected is True


@pytest.mark.django_db
def test_donate_resource_post_redirects_back_to_guild_detail(guild_member_client):
    client, guild, _member, manor = guild_member_client
    manor.silver = 200
    manor.save(update_fields=["silver"])

    response = client.post(
        reverse("guilds:donate"),
        {"resource_type": "silver", "amount": "100"},
        follow=False,
    )

    assert response.status_code == 302
    assert response["Location"].endswith(reverse("guilds:detail", args=[guild.id]))
