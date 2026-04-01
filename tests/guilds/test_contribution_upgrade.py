from __future__ import annotations

import pytest

from core.exceptions import GuildContributionError, GuildValidationError
from gameplay.models import InventoryItem, ItemTemplate, Manor
from guilds.models import GuildDonationLog, GuildMember, GuildResourceLog
from guilds.services import contribution as contribution_service
from guilds.services import guild as guild_service

pytest_plugins = ("tests.guilds.fixtures",)


@pytest.mark.django_db
class TestGuildContribution:
    def test_donate_resource(self, user_with_gold_bars):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="捐赠帮会", description="")

        manor = Manor.objects.get(user=user_with_gold_bars)
        manor.silver = 100000
        manor.save()

        member = GuildMember.objects.get(user=user_with_gold_bars, guild=guild)
        initial_contribution = member.total_contribution

        contribution_service.donate_resource(member, "silver", 10000)

        member.refresh_from_db()
        guild.refresh_from_db()
        manor.refresh_from_db()

        assert member.total_contribution > initial_contribution
        assert guild.silver == 10000
        assert manor.silver == 90000

    def test_donate_exceeds_daily_limit(self, user_with_gold_bars):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="限制帮会", description="")

        manor = Manor.objects.get(user=user_with_gold_bars)
        manor.silver = 10000000
        manor.save()

        member = GuildMember.objects.get(user=user_with_gold_bars, guild=guild)
        daily_limit = contribution_service.DAILY_DONATION_LIMITS.get("silver", 100000)

        with pytest.raises(GuildContributionError, match="已达上限"):
            contribution_service.donate_resource(member, "silver", daily_limit + 1)

    def test_donate_gold_bar_success(self, user_with_gold_bars):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="金条捐赠帮会", description="")
        member = GuildMember.objects.get(user=user_with_gold_bars, guild=guild)
        manor = Manor.objects.get(user=user_with_gold_bars)
        gold_bar_template = ItemTemplate.objects.get(key="gold_bar")
        gold_bar_item = InventoryItem.objects.get(
            manor=manor,
            template=gold_bar_template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )
        initial_quantity = gold_bar_item.quantity
        expected_contribution = 2 * contribution_service.CONTRIBUTION_RATES["gold_bar"]

        contribution_service.donate_resource(member, "gold_bar", 2)

        member.refresh_from_db()
        guild.refresh_from_db()
        gold_bar_item.refresh_from_db()

        assert guild.gold_bar == 2
        assert gold_bar_item.quantity == initial_quantity - 2
        assert member.total_contribution == expected_contribution
        assert GuildDonationLog.objects.filter(guild=guild, member=member, resource_type="gold_bar", amount=2).exists()
        assert GuildResourceLog.objects.filter(
            guild=guild,
            action="donation",
            gold_bar_change=2,
            related_user=user_with_gold_bars,
        ).exists()

    def test_donate_gold_bar_insufficient_inventory(self, user_with_gold_bars):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="金条不足帮会", description="")
        member = GuildMember.objects.get(user=user_with_gold_bars, guild=guild)
        manor = Manor.objects.get(user=user_with_gold_bars)
        gold_bar_template = ItemTemplate.objects.get(key="gold_bar")
        gold_bar_item = InventoryItem.objects.get(
            manor=manor,
            template=gold_bar_template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )
        gold_bar_item.quantity = 1
        gold_bar_item.save(update_fields=["quantity"])

        with pytest.raises(GuildContributionError, match="金条不足"):
            contribution_service.donate_resource(member, "gold_bar", 2)

    def test_donate_gold_bar_exceeds_daily_limit_after_partial_donation(self, user_with_gold_bars):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="金条日限帮会", description="")
        member = GuildMember.objects.get(user=user_with_gold_bars, guild=guild)
        manor = Manor.objects.get(user=user_with_gold_bars)
        gold_bar_template = ItemTemplate.objects.get(key="gold_bar")
        gold_bar_item = InventoryItem.objects.get(
            manor=manor,
            template=gold_bar_template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )
        gold_bar_item.quantity = 30
        gold_bar_item.save(update_fields=["quantity"])

        daily_limit = contribution_service.DAILY_DONATION_LIMITS["gold_bar"]
        contribution_service.donate_resource(member, "gold_bar", daily_limit - 1)

        with pytest.raises(GuildContributionError, match="今日金条捐赠已达上限"):
            contribution_service.donate_resource(member, "gold_bar", 2)


@pytest.mark.django_db
class TestGuildUpgrade:
    def test_upgrade_guild(self, user_with_gold_bars):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="升级帮会", description="")

        guild.gold_bar = 100
        guild.save()

        initial_level = guild.level

        guild_service.upgrade_guild(guild, user_with_gold_bars)
        guild.refresh_from_db()

        assert guild.level == initial_level + 1
        assert guild.gold_bar < 100

    def test_upgrade_max_level(self, user_with_gold_bars):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="满级帮会", description="")

        guild.level = 10
        guild.gold_bar = 10000
        guild.save()

        with pytest.raises(GuildValidationError, match="最高等级"):
            guild_service.upgrade_guild(guild, user_with_gold_bars)
