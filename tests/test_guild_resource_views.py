from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from battle.models import TroopTemplate
from core.exceptions import GuildWarehouseError
from gameplay.models import InventoryItem, ItemTemplate, PlayerTroop
from gameplay.services.manor.core import ensure_manor
from guilds.constants import DAILY_DONATION_LIMITS
from guilds.models import Guild, GuildApplication, GuildMember, GuildTroopDonationLog, GuildTroopStorage, GuildWarehouse
from guilds.services.warehouse import exchange_item


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
    manor.silver = 2_345
    manor.grain = 6_400
    manor.save(update_fields=["silver", "grain"])
    member.daily_donation_grain = max(0, int(DAILY_DONATION_LIMITS["grain"]) - 3_000)
    member.save(update_fields=["daily_donation_grain"])

    player_troop_template = TroopTemplate.objects.create(key="my_archer", name="我的弓兵")
    guild_troop_template = TroopTemplate.objects.create(key="guild_only_guard", name="库存护院")
    PlayerTroop.objects.create(manor=manor, troop_template=player_troop_template, count=6)
    GuildTroopStorage.objects.create(guild=guild, troop_template=guild_troop_template, count=9)

    response = client.get(reverse("guilds:detail", args=[guild.id]))

    assert response.status_code == 200
    assert response.context["donation_entries"]["silver"]["unit"] == 1_000
    assert response.context["donation_entries"]["grain"]["unit"] == 2_000
    assert response.context["donation_entries"]["silver"]["max_amount"] == 2_000
    assert response.context["donation_entries"]["grain"]["max_amount"] == 2_000
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
    assert 'id="silver_amount"' in body and 'min="1000"' in body and 'step="1000"' in body
    assert 'id="grain_amount"' in body and 'min="2000"' in body and 'step="2000"' in body
    assert 'max="2000"' in body
    assert "1000 银两 = 1 贡献度" in body
    assert "2000 粮食 = 1 贡献度" in body
    assert 'value="my_archer"' in body
    assert 'value="guild_only_guard"' not in body


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
    monkeypatch.setattr("guilds.constants.CONTRIBUTION_UNITS", {"silver": 2, "grain": 4, "gold_bar": 1})
    monkeypatch.setattr("guilds.constants.CONTRIBUTION_RATES", {"silver": 9, "grain": 8, "gold_bar": 7})
    monkeypatch.setattr("guilds.constants.DAILY_DONATION_LIMITS", {"silver": 5, "grain": 7, "gold_bar": 2})

    response = client.get(reverse("guilds:detail", args=[guild.id]))

    assert response.status_code == 200
    donation_entries = response.context["donation_entries"]
    assert donation_entries["silver"]["unit"] == 2
    assert donation_entries["silver"]["rate"] == 9
    assert donation_entries["silver"]["daily_limit"] == 5
    assert donation_entries["silver"]["max_amount"] == 4
    assert donation_entries["grain"]["unit"] == 4
    assert donation_entries["grain"]["rate"] == 8
    assert donation_entries["grain"]["daily_limit"] == 7
    assert donation_entries["grain"]["max_amount"] == 4
    assert donation_entries["gold_bar"]["unit"] == 1
    assert donation_entries["gold_bar"]["rate"] == 7
    assert donation_entries["gold_bar"]["daily_limit"] == 2


@pytest.mark.django_db
def test_guild_detail_page_shows_troop_contribution_allowance_and_tier_rate(guild_member_client):
    client, guild, member, manor = guild_member_client
    troop_template, _created = TroopTemplate.objects.get_or_create(key="dao_sheng", defaults={"name": "刀圣"})
    PlayerTroop.objects.create(manor=manor, troop_template=troop_template, count=30)
    GuildTroopDonationLog.objects.create(
        guild=guild,
        member=member,
        troop_template=troop_template,
        quantity=2,
    )

    response = client.get(reverse("guilds:detail", args=[guild.id]))

    assert response.status_code == 200
    troop_entry = response.context["troop_donation_entry"]
    assert troop_entry == {"donated_today": 24, "daily_limit": 300, "remaining_today": 276}
    troop = next(entry for entry in response.context["player_troops"] if entry.troop_template.key == "dao_sheng")
    assert troop.donation_rate == 12
    assert troop.max_donation_quantity == 23

    body = response.content.decode("utf-8")
    assert "今日护院贡献" in body
    assert "24 / 300" in body
    assert 'data-contribution-rate="12"' in body
    assert 'data-max-quantity="23"' in body


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

    body = response.content.decode("utf-8")
    assert "1000 银两 / 1 贡献" in body
    assert "2000 粮食 / 1 贡献" in body
    assert "1 金条 / 1200 贡献" in body


@pytest.mark.django_db
def test_guild_resource_exchange_view_accepts_quantity_larger_than_ordinary_item_limit(guild_member_client):
    client, guild, member, manor = guild_member_client
    manor.grain = 0
    manor.save(update_fields=["grain"])
    member.current_contribution = 10
    member.save(update_fields=["current_contribution"])
    GuildWarehouse.objects.create(guild=guild, item_key="grain", quantity=4_000, contribution_cost=1)

    response = client.post(
        reverse("guilds:exchange_item", args=["grain"]),
        {"quantity": "2000"},
        follow=False,
    )

    assert response.status_code == 302
    member.refresh_from_db()
    manor.refresh_from_db()
    grain_row = GuildWarehouse.objects.get(guild=guild, item_key="grain")
    assert grain_row.quantity == 2_000
    assert manor.grain == 2_000
    assert member.current_contribution == 9


@pytest.mark.django_db
def test_guild_warehouse_page_renders_production_items_without_item_templates(guild_member_client):
    client, guild, _member, _manor = guild_member_client

    ItemTemplate.objects.filter(key="guild_gear_box_master").delete()
    GuildWarehouse.objects.create(guild=guild, item_key="guild_gear_box_master", quantity=2, contribution_cost=800)

    response = client.get(reverse("guilds:warehouse"))

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "帮会宗师秘匣" in body
    assert "帮会满阶锻造工坊出品的秘匣，可开出一件高质量紫装。" in body
    assert "<h3>guild_gear_box_master</h3>" not in body
    assert "openExchangeModal('guild_gear_box_master'" in body


@pytest.mark.django_db
def test_guild_warehouse_exchange_creates_yaml_backed_loot_box_template(guild_member_client):
    _client, guild, member, manor = guild_member_client
    member.current_contribution = 1000
    member.save(update_fields=["current_contribution"])

    ItemTemplate.objects.filter(key="guild_gear_box_master").delete()
    GuildWarehouse.objects.create(guild=guild, item_key="guild_gear_box_master", quantity=1, contribution_cost=800)

    exchange_item(member, "guild_gear_box_master", 1)

    template = ItemTemplate.objects.get(key="guild_gear_box_master")
    assert template.name == "帮会宗师秘匣"
    assert template.effect_type == ItemTemplate.EffectType.LOOT_BOX
    assert template.is_usable is True
    assert template.effect_payload["gear_chance"] == 1
    assert template.effect_payload["gear_choices"][0]["item_key"] == "equip_qilinjia"
    inventory_item = InventoryItem.objects.get(
        manor=manor,
        template=template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    assert inventory_item.quantity == 1


@pytest.mark.django_db
def test_guild_warehouse_exchange_does_not_create_yaml_template_when_validation_fails(guild_member_client):
    _client, guild, member, _manor = guild_member_client
    member.current_contribution = 10
    member.save(update_fields=["current_contribution"])

    ItemTemplate.objects.filter(key="guild_gear_box_master").delete()
    GuildWarehouse.objects.create(guild=guild, item_key="guild_gear_box_master", quantity=1, contribution_cost=800)

    with pytest.raises(GuildWarehouseError, match="贡献度不足"):
        exchange_item(member, "guild_gear_box_master", 1)

    assert ItemTemplate.objects.filter(key="guild_gear_box_master").exists() is False


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


@pytest.mark.django_db
def test_donate_resource_rejects_get_before_rate_limit_side_effects(guild_member_client):
    client, _guild, _member, _manor = guild_member_client

    response = client.get(reverse("guilds:donate"))

    assert response.status_code == 405


@pytest.mark.django_db
def test_application_list_uses_reversed_reject_url_instead_of_hardcoded_path(guild_member_client, django_user_model):
    client, _guild, member, _manor = guild_member_client
    member.position = "admin"
    member.save(update_fields=["position"])
    applicant = django_user_model.objects.create_user(username="resource_view_applicant", password="pass12345")
    application = GuildApplication.objects.create(guild=member.guild, applicant=applicant, message="请收留")

    response = client.get(reverse("guilds:applications"))

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert reverse("guilds:reject_application", args=[application.id]) in body
    assert f"/guilds/applications/{application.id}/reject/" not in body
