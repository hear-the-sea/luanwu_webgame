from __future__ import annotations

import importlib
from datetime import timedelta
from pathlib import Path

import pytest
import yaml
from django.utils import timezone

ITEM_TEMPLATES_PATH = Path(__file__).resolve().parent.parent / "data" / "item_templates.yaml"


def _load_item_templates() -> dict[str, dict]:
    payload = yaml.safe_load(ITEM_TEMPLATES_PATH.read_text(encoding="utf-8"))
    return {row["key"]: row for row in payload["items"]}


def test_red_ruby_and_chunqiu_coin_are_distinct_resource_items():
    templates = _load_item_templates()

    assert templates["red_ruby"]["name"] == "红宝石"
    assert templates["red_ruby"]["effect_type"] == "resource"
    assert templates["chunqiu_coin"]["name"] == "春秋币"
    assert templates["chunqiu_coin"]["effect_type"] == "resource"
    assert templates["chunqiu_coin"]["description"] == (
        "流传于春秋旧世的珍稀货币，价值非凡，是少数势力才能掌握的高级资源。"
    )
    assert templates["chunqiu_coin"]["is_usable"] is False
    assert templates["chunqiu_coin"]["tradeable"] is True


class _CurrentModelApps:
    def get_model(self, app_label: str, model_name: str):
        from gameplay.models import GlobalMailCampaign, InventoryItem, ItemTemplate, Message
        from trade.models import MarketListing

        models = {
            ("gameplay", "GlobalMailCampaign"): GlobalMailCampaign,
            ("gameplay", "InventoryItem"): InventoryItem,
            ("gameplay", "ItemTemplate"): ItemTemplate,
            ("gameplay", "Message"): Message,
            ("trade", "MarketListing"): MarketListing,
        }
        return models[(app_label, model_name)]


@pytest.mark.django_db
def test_personal_red_ruby_migration_merges_inventory_and_keeps_guild_rows(django_user_model):
    from gameplay.models import GlobalMailCampaign, InventoryItem, ItemTemplate, Message
    from gameplay.services.manor.core import ensure_manor
    from guilds.models import Guild, GuildWarehouse
    from trade.models import MarketListing

    user = django_user_model.objects.create_user(username="chunqiu_coin_migration", password="pass12345")
    manor = ensure_manor(user)
    red_ruby = ItemTemplate.objects.create(key="red_ruby", name="红宝石", effect_type="resource")
    chunqiu_coin, _created = ItemTemplate.objects.update_or_create(
        key="chunqiu_coin",
        defaults={"name": "春秋币", "effect_type": "resource"},
    )
    InventoryItem.objects.create(
        manor=manor,
        template=red_ruby,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        quantity=7,
    )
    InventoryItem.objects.create(
        manor=manor,
        template=chunqiu_coin,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        quantity=3,
    )
    InventoryItem.objects.create(
        manor=manor,
        template=red_ruby,
        storage_location=InventoryItem.StorageLocation.TREASURY,
        quantity=2,
    )
    active_listing = MarketListing.objects.create(
        seller=manor,
        item_template=red_ruby,
        quantity=4,
        unit_price=500_000,
        total_price=2_000_000,
        duration=MarketListing.Duration.SHORT,
        listing_fee=1,
        expires_at=timezone.now() + timedelta(hours=1),
    )
    sold_listing = MarketListing.objects.create(
        seller=manor,
        item_template=red_ruby,
        quantity=1,
        unit_price=500_000,
        total_price=500_000,
        duration=MarketListing.Duration.SHORT,
        listing_fee=1,
        expires_at=timezone.now() - timedelta(hours=1),
        status=MarketListing.Status.SOLD,
    )
    expired_listing = MarketListing.objects.create(
        seller=manor,
        item_template=red_ruby,
        quantity=2,
        unit_price=500_000,
        total_price=1_000_000,
        duration=MarketListing.Duration.SHORT,
        listing_fee=1,
        expires_at=timezone.now() - timedelta(hours=1),
        status=MarketListing.Status.EXPIRED,
    )
    cancelled_listing = MarketListing.objects.create(
        seller=manor,
        item_template=red_ruby,
        quantity=3,
        unit_price=500_000,
        total_price=1_500_000,
        duration=MarketListing.Duration.SHORT,
        listing_fee=1,
        expires_at=timezone.now() + timedelta(hours=1),
        status=MarketListing.Status.CANCELLED,
    )
    unclaimed_message = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="待领取红宝石",
        attachments={
            "resources": {"silver": 100},
            "items": {"red_ruby": 4, "chunqiu_coin": 2},
        },
    )
    claimed_message = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="已领取红宝石",
        attachments={
            "items": {"red_ruby": 5},
            "claimed": {"items": {"red_ruby": 5}},
        },
        is_claimed=True,
    )
    campaign = GlobalMailCampaign.objects.create(
        key="red-ruby-personal-reward",
        kind=Message.Kind.REWARD,
        title="红宝石活动奖励",
        attachments={"items": {"red_ruby": 6, "chunqiu_coin": 1}},
    )
    inactive_campaign = GlobalMailCampaign.objects.create(
        key="red-ruby-inactive-reward",
        kind=Message.Kind.REWARD,
        title="已停用红宝石活动奖励",
        attachments={"items": {"red_ruby": 8}},
        is_active=False,
    )
    ended_campaign = GlobalMailCampaign.objects.create(
        key="red-ruby-ended-reward",
        kind=Message.Kind.REWARD,
        title="已结束红宝石活动奖励",
        attachments={"items": {"red_ruby": 9}},
        end_at=timezone.now() - timedelta(minutes=1),
    )
    future_campaign = GlobalMailCampaign.objects.create(
        key="red-ruby-future-reward",
        kind=Message.Kind.REWARD,
        title="未来红宝石活动奖励",
        attachments={"items": {"red_ruby": 10}},
        start_at=timezone.now() + timedelta(hours=1),
        end_at=timezone.now() + timedelta(days=1),
    )
    empty_message = Message.objects.create(
        manor=manor,
        kind=Message.Kind.SYSTEM,
        title="空附件消息",
        attachments={},
    )
    malformed_message = Message.objects.create(
        manor=manor,
        kind=Message.Kind.SYSTEM,
        title="非字典附件消息",
        attachments=["red_ruby"],
    )
    guild = Guild.objects.create(name="春秋迁移帮会", founder=user)
    guild_ruby = GuildWarehouse.objects.create(guild=guild, item_key="red_ruby", quantity=11)

    migration = importlib.import_module("gameplay.migrations.0131_split_personal_red_ruby_to_chunqiu_coin")
    migration.migrate_personal_red_ruby(_CurrentModelApps(), None)
    migration.migrate_personal_red_ruby(_CurrentModelApps(), None)

    assert not InventoryItem.objects.filter(manor=manor, template__key="red_ruby").exists()
    assert (
        InventoryItem.objects.get(
            manor=manor,
            template__key="chunqiu_coin",
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        ).quantity
        == 10
    )
    assert (
        InventoryItem.objects.get(
            manor=manor,
            template__key="chunqiu_coin",
            storage_location=InventoryItem.StorageLocation.TREASURY,
        ).quantity
        == 2
    )
    active_listing.refresh_from_db()
    sold_listing.refresh_from_db()
    expired_listing.refresh_from_db()
    cancelled_listing.refresh_from_db()
    assert active_listing.item_template.key == "chunqiu_coin"
    assert sold_listing.item_template.key == "red_ruby"
    assert expired_listing.item_template.key == "red_ruby"
    assert cancelled_listing.item_template.key == "red_ruby"
    unclaimed_message.refresh_from_db()
    claimed_message.refresh_from_db()
    assert unclaimed_message.attachments == {
        "resources": {"silver": 100},
        "items": {"chunqiu_coin": 6},
    }
    assert claimed_message.attachments == {
        "items": {"red_ruby": 5},
        "claimed": {"items": {"red_ruby": 5}},
    }
    campaign.refresh_from_db()
    assert campaign.attachments == {"items": {"chunqiu_coin": 7}}
    inactive_campaign.refresh_from_db()
    ended_campaign.refresh_from_db()
    future_campaign.refresh_from_db()
    assert inactive_campaign.attachments == {"items": {"red_ruby": 8}}
    assert ended_campaign.attachments == {"items": {"red_ruby": 9}}
    assert future_campaign.attachments == {"items": {"chunqiu_coin": 10}}
    empty_message.refresh_from_db()
    malformed_message.refresh_from_db()
    assert empty_message.attachments == {}
    assert malformed_message.attachments == ["red_ruby"]
    guild_ruby.refresh_from_db()
    assert guild_ruby.item_key == "red_ruby"
    assert guild_ruby.quantity == 11


def test_personal_red_ruby_migration_depends_on_market_listing_schema():
    migration = importlib.import_module("gameplay.migrations.0131_split_personal_red_ruby_to_chunqiu_coin")

    assert ("trade", "0003_marketlisting_markettransaction_and_more") in migration.Migration.dependencies
