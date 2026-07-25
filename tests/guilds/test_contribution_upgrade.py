from __future__ import annotations

import pytest
from django.utils import timezone

import guilds.constants as guild_constants
from core.exceptions import GuildContributionError, GuildPermissionError, GuildValidationError
from gameplay.models import InventoryItem, ItemTemplate, Manor
from guilds.models import Guild, GuildDonationLog, GuildMember, GuildResourceLog, GuildWarehouse
from guilds.services import contribution as contribution_service
from guilds.services import guild as guild_service

pytest_plugins = ("tests.guilds.fixtures",)


@pytest.mark.django_db
class TestGuildContribution:
    def test_donate_resource(self, user_with_gold_bars):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="捐赠帮会", description="")

        manor = Manor.objects.get(user=user_with_gold_bars)
        manor.silver = 100000
        manor.silver_capacity = 100000
        manor.save(update_fields=["silver", "silver_capacity"])

        member = GuildMember.objects.get(user=user_with_gold_bars, guild=guild)
        initial_contribution = member.total_contribution

        contribution_service.donate_resource(member, "silver", 10_000)

        member.refresh_from_db()
        guild.refresh_from_db()
        manor.refresh_from_db()

        assert member.total_contribution == initial_contribution + 10
        assert member.current_contribution == 10
        assert member.weekly_contribution == 10
        assert guild.silver == 10_000
        assert manor.silver == 90_000

    def test_donate_exceeds_daily_limit(self, user_with_gold_bars):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="限制帮会", description="")

        manor = Manor.objects.get(user=user_with_gold_bars)
        manor.silver = 10000000
        manor.save()

        member = GuildMember.objects.get(user=user_with_gold_bars, guild=guild)
        daily_limit = guild_constants.DAILY_DONATION_LIMITS.get("silver", 100000)

        with pytest.raises(GuildContributionError, match="已达上限"):
            contribution_service.donate_resource(
                member,
                "silver",
                daily_limit + guild_constants.CONTRIBUTION_UNITS["silver"],
            )

    @pytest.mark.parametrize(
        ("resource_type", "amount", "resource_field"),
        [("silver", 999, "silver"), ("grain", 1999, "grain")],
    )
    def test_donate_resource_rejects_partial_contribution_bundle(
        self,
        user_with_gold_bars,
        resource_type,
        amount,
        resource_field,
    ):
        guild = guild_service.create_guild(user=user_with_gold_bars, name=f"{resource_type}整包帮会", description="")
        member = GuildMember.objects.get(user=user_with_gold_bars, guild=guild)
        manor = Manor.objects.get(user=user_with_gold_bars)
        setattr(manor, resource_field, 10_000)
        manor.save(update_fields=[resource_field])

        with pytest.raises(GuildContributionError, match="整数倍"):
            contribution_service.donate_resource(member, resource_type, amount)

        member.refresh_from_db()
        manor.refresh_from_db()
        assert getattr(manor, resource_field) == 10_000
        assert member.total_contribution == 0
        assert member.current_contribution == 0
        assert member.weekly_contribution == 0
        assert not GuildDonationLog.objects.filter(member=member).exists()

    def test_donate_grain_uses_approved_bundle_rate(self, user_with_gold_bars):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="粮食整包帮会", description="")
        member = GuildMember.objects.get(user=user_with_gold_bars, guild=guild)
        manor = Manor.objects.get(user=user_with_gold_bars)
        manor.grain = 4_000
        manor.save(update_fields=["grain"])

        contribution_service.donate_resource(member, "grain", 2_000)

        member.refresh_from_db()
        manor.refresh_from_db()
        warehouse_row = GuildWarehouse.objects.get(guild=guild, item_key="grain")
        assert member.total_contribution == 1
        assert member.current_contribution == 1
        assert member.weekly_contribution == 1
        assert manor.grain == 2_000
        assert warehouse_row.quantity == 2_000
        assert warehouse_row.contribution_cost == 1

    def test_donate_resource_uses_latest_runtime_min_amount(self, user_with_gold_bars, monkeypatch):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="动态下限帮会", description="")

        manor = Manor.objects.get(user=user_with_gold_bars)
        manor.silver = 100000
        manor.silver_capacity = 100000
        manor.save(update_fields=["silver", "silver_capacity"])

        member = GuildMember.objects.get(user=user_with_gold_bars, guild=guild)
        monkeypatch.setattr("guilds.constants.MIN_DONATION_AMOUNT", 50)

        with pytest.raises(GuildContributionError, match="单次捐赠最少50单位"):
            contribution_service.donate_resource(member, "silver", 40)

    def test_donate_resource_uses_latest_runtime_contribution_rate(self, user_with_gold_bars, monkeypatch):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="动态倍率帮会", description="")

        manor = Manor.objects.get(user=user_with_gold_bars)
        manor.silver = 100000
        manor.save()

        member = GuildMember.objects.get(user=user_with_gold_bars, guild=guild)
        monkeypatch.setattr("guilds.constants.CONTRIBUTION_RATES", {"silver": 7, "grain": 2, "gold_bar": 50})

        contribution_service.donate_resource(member, "silver", 1_000)

        member.refresh_from_db()
        guild.refresh_from_db()
        manor.refresh_from_db()

        assert member.total_contribution == 7
        assert member.current_contribution == 7
        assert guild.silver == 1_000
        assert manor.silver == 99_000

    def test_donate_resource_rejects_member_moved_to_another_guild(self, user_with_gold_bars):
        old_guild = guild_service.create_guild(user=user_with_gold_bars, name="旧捐献帮会", description="")
        member = GuildMember.objects.get(user=user_with_gold_bars, guild=old_guild)
        new_guild = Guild.objects.create(name="新捐献帮会", founder=user_with_gold_bars, is_active=True)
        GuildMember.objects.filter(pk=member.pk).update(guild=new_guild, current_contribution=0)
        manor = Manor.objects.get(user=user_with_gold_bars)
        manor.silver = 100000
        manor.save(update_fields=["silver"])

        with pytest.raises(GuildContributionError, match="您不在帮会中"):
            contribution_service.donate_resource(member, "silver", 10000)

        old_guild.refresh_from_db()
        new_guild.refresh_from_db()
        manor.refresh_from_db()
        current_member = GuildMember.objects.get(pk=member.pk)

        assert old_guild.silver == 0
        assert new_guild.silver == 0
        assert manor.silver == 100000
        assert current_member.current_contribution == 0
        assert GuildDonationLog.objects.filter(member=current_member).exists() is False

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
        expected_contribution = 2 * guild_constants.CONTRIBUTION_RATES["gold_bar"]

        contribution_service.donate_resource(member, "gold_bar", 2)

        member.refresh_from_db()
        guild.refresh_from_db()
        gold_bar_item.refresh_from_db()
        warehouse_row = GuildWarehouse.objects.get(guild=guild, item_key="gold_bar")

        assert guild.gold_bar == 0
        assert warehouse_row.quantity == 2
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

        daily_limit = guild_constants.DAILY_DONATION_LIMITS["gold_bar"]
        contribution_service.donate_resource(member, "gold_bar", daily_limit - 1)

        with pytest.raises(GuildContributionError, match="今日金条捐赠已达上限"):
            contribution_service.donate_resource(member, "gold_bar", 2)

    def test_donate_gold_bar_uses_member_daily_counter_as_authoritative_limit(self, user_with_gold_bars):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="金条计数帮会", description="")
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

        daily_limit = guild_constants.DAILY_DONATION_LIMITS["gold_bar"]
        member.daily_donation_reset_at = timezone.localdate()
        member.daily_donation_gold_bar = daily_limit - 1
        member.save(update_fields=["daily_donation_reset_at", "daily_donation_gold_bar"])
        GuildDonationLog.objects.filter(member=member, resource_type="gold_bar").delete()

        with pytest.raises(GuildContributionError, match="今日金条捐赠已达上限"):
            contribution_service.donate_resource(member, "gold_bar", 2)


@pytest.mark.django_db
class TestGuildUpgrade:
    def test_upgrade_guild(self, user_with_gold_bars):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="升级帮会", description="")
        GuildWarehouse.objects.create(guild=guild, item_key="gold_bar", quantity=100, contribution_cost=50)

        initial_level = guild.level

        guild_service.upgrade_guild(guild, user_with_gold_bars)
        guild.refresh_from_db()

        remaining_row = GuildWarehouse.objects.get(guild=guild, item_key="gold_bar")
        assert guild.level == initial_level + 1
        assert remaining_row.quantity < 100
        assert remaining_row.total_exchanged == guild_constants.GUILD_UPGRADE_BASE_COST

    def test_upgrade_guild_rechecks_leader_permission_inside_transaction(self, user_with_gold_bars, monkeypatch):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="升级竞态帮会", description="")
        leader_member = GuildMember.objects.get(user=user_with_gold_bars, guild=guild)
        GuildWarehouse.objects.create(guild=guild, item_key="gold_bar", quantity=100, contribution_cost=50)

        real_get_active_membership = guild_service.get_active_membership

        def _demote_after_initial_check(*args, **kwargs):
            resolved = real_get_active_membership(*args, **kwargs)
            GuildMember.objects.filter(pk=leader_member.pk).update(position="member")
            return resolved

        monkeypatch.setattr("guilds.services.guild.get_active_membership", _demote_after_initial_check)

        with pytest.raises(GuildPermissionError, match="只有帮主可以升级帮会"):
            guild_service.upgrade_guild(guild, user_with_gold_bars)

        guild.refresh_from_db()
        warehouse_row = GuildWarehouse.objects.get(guild=guild, item_key="gold_bar")
        assert guild.level == 1
        assert warehouse_row.quantity == 100

    def test_upgrade_max_level(self, user_with_gold_bars):
        guild = guild_service.create_guild(user=user_with_gold_bars, name="满级帮会", description="")

        guild.level = 10
        guild.save()
        GuildWarehouse.objects.create(guild=guild, item_key="gold_bar", quantity=10000, contribution_cost=50)

        with pytest.raises(GuildValidationError, match="最高等级"):
            guild_service.upgrade_guild(guild, user_with_gold_bars)
