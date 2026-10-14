from __future__ import annotations

import pytest
from django.utils import timezone

from core.exceptions import GuildContributionError
from gameplay.models import InventoryItem, ItemTemplate, Manor
from guilds.models import GuildDonationLog, GuildMember, GuildWarehouse
from guilds.services import contribution as contribution_service
from guilds.services import guild as guild_service

pytest_plugins = ("tests.guilds.fixtures",)
pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "item_key,quantity,bound_quantity,donate_amount",
    [
        ("gold_bar", 4, 3, 2),
        ("grain", 4000, 3000, 2000),
    ],
)
def test_guild_donation_rejects_bound_stock_without_credited_contribution(
    user_with_gold_bars, item_key, quantity, bound_quantity, donate_amount
):
    guild = guild_service.create_guild(user=user_with_gold_bars, name="绑定捐献检查", description="")
    member = GuildMember.objects.get(user=user_with_gold_bars, guild=guild)
    manor = Manor.objects.get(user=user_with_gold_bars)
    manor.grain = quantity if item_key == "grain" else manor.grain
    manor.resource_updated_at = timezone.now()
    manor.save(update_fields=["grain", "resource_updated_at"])
    template, _ = ItemTemplate.objects.get_or_create(key=item_key, defaults={"name": item_key})
    stock, _ = InventoryItem.objects.update_or_create(
        manor=manor,
        template=template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        defaults={"quantity": quantity, "bound_quantity": bound_quantity},
    )
    with pytest.raises(GuildContributionError):
        contribution_service.donate_resource(member, item_key, donate_amount)
    stock.refresh_from_db()
    member.refresh_from_db()
    assert (stock.quantity, stock.bound_quantity) == (quantity, bound_quantity)
    assert member.current_contribution == 0
    assert not GuildDonationLog.objects.filter(member=member).exists()
    assert not GuildWarehouse.objects.filter(guild=guild, item_key=item_key).exists()


@pytest.mark.parametrize(
    "item_key,quantity,bound_quantity,donate_amount",
    [
        ("gold_bar", 5, 3, 2),
        ("grain", 5000, 3000, 2000),
    ],
)
def test_guild_donation_keeps_bound_remainder(user_with_gold_bars, item_key, quantity, bound_quantity, donate_amount):
    guild = guild_service.create_guild(user=user_with_gold_bars, name="非绑捐献检查", description="")
    member = GuildMember.objects.get(user=user_with_gold_bars, guild=guild)
    manor = Manor.objects.get(user=user_with_gold_bars)
    manor.grain = quantity if item_key == "grain" else manor.grain
    manor.resource_updated_at = timezone.now()
    manor.save(update_fields=["grain", "resource_updated_at"])
    template, _ = ItemTemplate.objects.get_or_create(key=item_key, defaults={"name": item_key})
    stock, _ = InventoryItem.objects.update_or_create(
        manor=manor,
        template=template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        defaults={"quantity": quantity, "bound_quantity": bound_quantity},
    )
    contribution_service.donate_resource(member, item_key, donate_amount)
    stock.refresh_from_db()
    assert (stock.quantity, stock.bound_quantity) == (bound_quantity, bound_quantity)
    assert GuildWarehouse.objects.get(guild=guild, item_key=item_key).quantity == donate_amount
    if item_key == "grain":
        manor.refresh_from_db()
        assert manor.grain == bound_quantity


def test_donation_page_excludes_bound_stock_and_frozen_gold(user_with_gold_bars):
    from guilds.views.contribution import build_guild_resource_context
    from trade.models import FrozenGoldBar

    guild = guild_service.create_guild(user=user_with_gold_bars, name="捐赠可用量检查", description="")
    member = GuildMember.objects.get(user=user_with_gold_bars, guild=guild)
    manor = Manor.objects.get(user=user_with_gold_bars)
    manor.grain = 6000
    manor.save(update_fields=["grain"])
    for key, quantity, bound in [("grain", 6000, 3000), ("gold_bar", 10, 4)]:
        template, _ = ItemTemplate.objects.get_or_create(key=key, defaults={"name": key})
        InventoryItem.objects.update_or_create(
            manor=manor,
            template=template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            defaults={"quantity": quantity, "bound_quantity": bound},
        )
    FrozenGoldBar.objects.create(manor=manor, amount=5, is_frozen=True)

    entries = build_guild_resource_context(member, manor=manor)["donation_entries"]
    assert entries["grain"]["available"] == 3000
    assert entries["grain"]["max_amount"] <= 3000
    assert entries["gold_bar"]["available"] == 1
    assert entries["gold_bar"]["max_amount"] <= 1
