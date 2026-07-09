from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from gameplay.models import InventoryItem, ItemTemplate, Message, RaidRun, ResourceEvent
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestArchetype, GuestRarity, GuestStatus, GuestTemplate
from guilds.models import GuildDonationLog, GuildMember, GuildWarehouse
from tests.helpers.auction import create_active_round_slot, ensure_gold_bar_template
from trade.models import AuctionBid, AuctionDelivery, FrozenGoldBar, ShopPurchaseLog, ShopSellLog
from trade.services.auction.rounds import _settle_slot
from trade.services.shop_config import ShopItemConfig

pytestmark = [pytest.mark.django_db, pytest.mark.e2e]


def _create_user_manor(django_user_model, username: str, *, password: str = "pass12345"):
    user = django_user_model.objects.create_user(username=username, password=password)
    manor = ensure_manor(user)
    manor.newbie_protection_until = None
    manor.peace_shield_until = None
    manor.defeat_protection_until = None
    manor.save(update_fields=["newbie_protection_until", "peace_shield_until", "defeat_protection_until"])
    return user, manor


def _force_login(client, user):
    client.force_login(user)
    return client


def _ensure_item_template(key: str, *, name: str, price: int = 100) -> ItemTemplate:
    template, _ = ItemTemplate.objects.get_or_create(
        key=key,
        defaults={
            "name": name,
            "price": price,
            "effect_type": ItemTemplate.EffectType.TOOL,
            "is_usable": False,
            "tradeable": True,
        },
    )
    if template.price != price:
        template.price = price
        template.save(update_fields=["price"])
    return template


def _grant_item(manor, template: ItemTemplate, quantity: int) -> InventoryItem:
    item, _ = InventoryItem.objects.get_or_create(
        manor=manor,
        template=template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        defaults={"quantity": 0},
    )
    item.quantity = quantity
    item.save(update_fields=["quantity"])
    return item


def test_e2e_auction_bid_settlement_message_claims_reward(client, django_user_model, monkeypatch):
    user, manor = _create_user_manor(django_user_model, "e2e_auction_winner")
    _force_login(client, user)
    gold_template = ensure_gold_bar_template()
    _grant_item(manor, gold_template, 20)
    slot = create_active_round_slot(
        item_key="e2e_auction_reward_item",
        round_number=91001,
        starting_price=10,
        min_increment=1,
    )
    monkeypatch.setattr("trade.services.auction.rounds.notify_user", lambda *_args, **_kwargs: True)

    response = client.post(reverse("trade:auction_bid", args=[slot.id]), {"amount": "12"})

    assert response.status_code == 302
    bid = AuctionBid.objects.get(slot=slot, manor=manor)
    assert bid.status == AuctionBid.Status.ACTIVE
    assert FrozenGoldBar.objects.get(auction_bid=bid).amount == 12
    assert InventoryItem.objects.get(manor=manor, template=gold_template).quantity == 20

    slot.round.end_at = timezone.now() - timedelta(minutes=1)
    slot.round.save(update_fields=["end_at"])
    with TestCase.captureOnCommitCallbacks(execute=True):
        result = _settle_slot(slot)

    assert result["sold"] is True
    bid.refresh_from_db()
    assert bid.status == AuctionBid.Status.WON
    assert AuctionDelivery.objects.get(bid=bid).status == AuctionDelivery.Status.DELIVERED
    assert InventoryItem.objects.get(manor=manor, template=gold_template).quantity == 8
    message = Message.objects.get(manor=manor, title__contains="拍卖行")
    assert message.attachments["items"][slot.item_template.key] == 1

    claim_response = client.post(
        reverse("gameplay:claim_attachment", kwargs={"pk": message.pk}),
        HTTP_ACCEPT="application/json",
    )

    assert claim_response.status_code == 200
    message.refresh_from_db()
    assert message.is_claimed is True
    assert InventoryItem.objects.get(manor=manor, template=slot.item_template).quantity == 1


def test_e2e_shop_buy_then_sell_updates_inventory_silver_and_logs(client, django_user_model, monkeypatch):
    user, manor = _create_user_manor(django_user_model, "e2e_shop_user")
    _force_login(client, user)
    manor.silver = 1000
    manor.silver_capacity = 2000
    manor.save(update_fields=["silver", "silver_capacity"])
    template = _ensure_item_template("e2e_shop_item", name="端到端商铺物品", price=50)
    monkeypatch.setattr(
        "trade.services.shop_service.get_shop_item_config",
        lambda item_key: (
            ShopItemConfig(item_key=item_key, price=50, stock=-1, daily_refresh=False)
            if item_key == template.key
            else None
        ),
    )

    buy_response = client.post(reverse("trade:shop_buy"), {"item_key": template.key, "quantity": "2"})

    assert buy_response.status_code == 302
    manor.refresh_from_db()
    assert manor.silver == 850
    assert InventoryItem.objects.get(manor=manor, template=template).quantity == 2
    assert ShopPurchaseLog.objects.filter(manor=manor, item_key=template.key, quantity=2, total_cost=150).exists()

    sell_response = client.post(reverse("trade:shop_sell"), {"item_key": template.key, "quantity": "1"})

    assert sell_response.status_code == 302
    manor.refresh_from_db()
    assert manor.silver == 900
    assert InventoryItem.objects.get(manor=manor, template=template).quantity == 1
    assert ShopSellLog.objects.filter(manor=manor, item_key=template.key, quantity=1, total_income=50).exists()


def test_e2e_raid_api_starts_march_and_records_incoming_message(client, django_user_model, monkeypatch):
    attacker_user, attacker = _create_user_manor(django_user_model, "e2e_raid_attacker")
    _force_login(client, attacker_user)
    _defender_user, defender = _create_user_manor(django_user_model, "e2e_raid_defender")
    guest_template = GuestTemplate.objects.create(
        key="e2e_raid_guest_template",
        name="端到端出征门客",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GREEN,
        base_attack=100,
        base_intellect=80,
        base_defense=90,
        base_agility=70,
        base_luck=50,
        base_hp=1200,
    )
    guest = Guest.objects.create(
        manor=attacker,
        template=guest_template,
        level=10,
        status=GuestStatus.IDLE,
        force=120,
        intellect=80,
        defense_stat=90,
        agility=70,
        current_hp=1200,
    )
    monkeypatch.setattr("gameplay.services.raid.combat.runs.calculate_raid_travel_time", lambda *_args: 30)
    monkeypatch.setattr("gameplay.services.raid.combat.runs._dispatch_raid_battle_task", lambda *_args: None)

    response = client.post(
        reverse("gameplay:start_raid_api"),
        data=json.dumps({"target_id": defender.id, "guest_ids": [guest.id], "troop_loadout": {}}),
        content_type="application/json",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    run = RaidRun.objects.get(pk=payload["raid_id"])
    assert run.attacker == attacker
    assert run.defender == defender
    assert run.status == RaidRun.Status.MARCHING
    assert run.travel_time == 30
    guest.refresh_from_db()
    attacker.refresh_from_db()
    assert guest.status == GuestStatus.DEPLOYED
    assert attacker.action_points == 990
    assert Message.objects.filter(manor=defender, title__contains="敌军来袭").exists()


def test_e2e_message_attachment_json_claim_is_idempotent_for_user(client, django_user_model):
    user, manor = _create_user_manor(django_user_model, "e2e_message_user")
    _force_login(client, user)
    template = _ensure_item_template("e2e_message_reward_item", name="端到端邮件奖励", price=10)
    message = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="端到端奖励邮件",
        attachments={"items": {template.key: 3}, "resources": {"silver": 100}},
    )

    first_response = client.post(
        reverse("gameplay:claim_attachment", kwargs={"pk": message.pk}),
        HTTP_ACCEPT="application/json",
    )
    second_response = client.post(
        reverse("gameplay:claim_attachment", kwargs={"pk": message.pk}),
        HTTP_ACCEPT="application/json",
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 400
    manor.refresh_from_db()
    message.refresh_from_db()
    assert manor.silver == 5100
    assert message.is_claimed is True
    assert InventoryItem.objects.get(manor=manor, template=template).quantity == 3
    assert ResourceEvent.objects.filter(manor=manor, resource_type="silver", delta=100).exists()


def test_e2e_guild_create_then_donate_gold_bar_updates_member_and_warehouse(client, django_user_model):
    user, manor = _create_user_manor(django_user_model, "e2e_guild_leader")
    _force_login(client, user)
    gold_template = ensure_gold_bar_template()
    _grant_item(manor, gold_template, 20)

    create_response = client.post(
        reverse("guilds:create"),
        {"name": "端到端帮会", "description": "E2E", "emblem": "default"},
    )

    assert create_response.status_code == 302
    member = GuildMember.objects.select_related("guild").get(user=user)
    remaining_after_create = InventoryItem.objects.get(manor=manor, template=gold_template).quantity
    assert member.position == "leader"
    assert remaining_after_create < 20

    donate_response = client.post(reverse("guilds:donate"), {"resource_type": "gold_bar", "amount": "2"})

    assert donate_response.status_code == 302
    member.refresh_from_db()
    gold_item = InventoryItem.objects.get(manor=manor, template=gold_template)
    warehouse = GuildWarehouse.objects.get(guild=member.guild, item_key="gold_bar")
    assert gold_item.quantity == remaining_after_create - 2
    assert warehouse.quantity == 2
    assert member.total_contribution > 0
    assert GuildDonationLog.objects.filter(
        guild=member.guild, member=member, resource_type="gold_bar", amount=2
    ).exists()
