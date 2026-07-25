from __future__ import annotations

from django.db import migrations
from django.db.models import Q
from django.utils import timezone

CHUNQIU_COIN_DEFAULTS = {
    "name": "春秋币",
    "description": "流传于春秋旧世的珍稀货币，价值非凡，是少数势力才能掌握的高级资源。",
    "effect_type": "resource",
    "effect_payload": {},
    "icon": "",
    "rarity": "blue",
    "tradeable": True,
    "price": 500000,
    "storage_space": 150,
    "is_usable": False,
}

RED_RUBY_KEY = "red_ruby"
CHUNQIU_COIN_KEY = "chunqiu_coin"
ACTIVE_LISTING_STATUS = "active"


def _objects_for_schema(model, schema_editor):
    """Use the migration connection while keeping direct unit-test calls convenient."""
    connection = getattr(schema_editor, "connection", None)
    database_alias = getattr(connection, "alias", None)
    return model.objects.using(database_alias) if database_alias else model.objects


def _attachment_quantity(value):
    """Return a lossless non-negative integer, or None for malformed attachment data."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer():
        integer_value = int(value)
        return integer_value if integer_value >= 0 else None
    if isinstance(value, str):
        try:
            integer_value = int(value)
        except (TypeError, ValueError):
            return None
        return integer_value if integer_value >= 0 else None
    return None


def _rewrite_unclaimed_item_attachments(attachments):
    """Merge a top-level red-ruby item bucket without touching audit metadata."""
    if not isinstance(attachments, dict):
        return attachments, False

    items = attachments.get("items")
    if not isinstance(items, dict) or RED_RUBY_KEY not in items:
        return attachments, False

    red_ruby_quantity = _attachment_quantity(items[RED_RUBY_KEY])
    if red_ruby_quantity is None:
        return attachments, False

    existing_coin_quantity = 0
    if CHUNQIU_COIN_KEY in items:
        existing_coin_quantity = _attachment_quantity(items[CHUNQIU_COIN_KEY])
        if existing_coin_quantity is None:
            return attachments, False

    rewritten_items = dict(items)
    del rewritten_items[RED_RUBY_KEY]
    rewritten_items[CHUNQIU_COIN_KEY] = existing_coin_quantity + red_ruby_quantity
    rewritten_attachments = dict(attachments)
    rewritten_attachments["items"] = rewritten_items
    return rewritten_attachments, True


def migrate_personal_red_ruby(apps, schema_editor):
    ItemTemplate = apps.get_model("gameplay", "ItemTemplate")
    InventoryItem = apps.get_model("gameplay", "InventoryItem")
    Message = apps.get_model("gameplay", "Message")
    GlobalMailCampaign = apps.get_model("gameplay", "GlobalMailCampaign")
    MarketListing = apps.get_model("trade", "MarketListing")

    item_templates = _objects_for_schema(ItemTemplate, schema_editor)
    inventory_items = _objects_for_schema(InventoryItem, schema_editor)
    messages = _objects_for_schema(Message, schema_editor)
    campaigns = _objects_for_schema(GlobalMailCampaign, schema_editor)
    listings = _objects_for_schema(MarketListing, schema_editor)

    chunqiu_coin, _created = item_templates.update_or_create(
        key="chunqiu_coin",
        defaults=CHUNQIU_COIN_DEFAULTS,
    )
    red_ruby = item_templates.filter(key=RED_RUBY_KEY).first()

    if red_ruby is not None:
        personal_ruby_rows = inventory_items.filter(template_id=red_ruby.pk).order_by("pk")
        for ruby_item in personal_ruby_rows.iterator():
            coin_item, _created = inventory_items.get_or_create(
                manor_id=ruby_item.manor_id,
                template_id=chunqiu_coin.pk,
                storage_location=ruby_item.storage_location,
                defaults={"quantity": 0},
            )
            coin_item.quantity = int(coin_item.quantity or 0) + int(ruby_item.quantity or 0)
            coin_item.save(update_fields=["quantity"])
            inventory_items.filter(pk=ruby_item.pk).delete()

        # Historical trade rows must keep their status and audit fields. Only an
        # actually active listing can still deliver the listed item.
        listings.filter(
            item_template_id=red_ruby.pk,
            status=ACTIVE_LISTING_STATUS,
        ).update(item_template_id=chunqiu_coin.pk)

    # Claimed messages are historical receipts: changing either their original
    # item bucket or the nested ``claimed`` payload would destroy the audit trail.
    for message in messages.filter(is_claimed=False).only("pk", "attachments").iterator():
        rewritten_attachments, changed = _rewrite_unclaimed_item_attachments(message.attachments)
        if changed:
            messages.filter(pk=message.pk).update(attachments=rewritten_attachments)

    # Keep inactive and already-ended campaigns untouched for audit purposes.
    # A future-start campaign is still deliverable once it starts, so it is
    # intentionally included as long as its end date has not passed.
    current_time = timezone.now()
    deliverable_campaigns = campaigns.filter(is_active=True).filter(
        Q(end_at__isnull=True) | Q(end_at__gte=current_time)
    )
    for campaign in deliverable_campaigns.only("pk", "attachments").iterator():
        rewritten_attachments, changed = _rewrite_unclaimed_item_attachments(campaign.attachments)
        if changed:
            campaigns.filter(pk=campaign.pk).update(attachments=rewritten_attachments)


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0130_jail_recruitment_attempt_scope"),
        ("trade", "0003_marketlisting_markettransaction_and_more"),
    ]

    operations = [
        migrations.RunPython(migrate_personal_red_ruby, migrations.RunPython.noop),
    ]
